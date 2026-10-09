"""Persist verified full histories; recover ONLY the identical report session.

This is a data checkpoint, never an order or a synthetic new close. JSON and CSV
only; no executable deserialization. The Actions workflow saves only after the
whole model generation succeeds and never saves replay or PR runs.
"""
from __future__ import annotations

import hashlib
from io import StringIO
import json
import os
from pathlib import Path

import pandas as pd

import monitor
from ndx_fallback import utc

VERSION = 'verified-full-history-v1'
MAX_BYTES = 2_000_000


def directory():
    value = os.environ.get('PRICE_CHECKPOINT_DIR')
    return Path(value) if value else None


def _read(path):
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Unsafe or oversized checkpoint file')
    return path.read_bytes()


def _rules_hash():
    # A strategy/validation change invalidates old checkpoints instead of silently
    # treating old inputs as newly validated under a changed contract.
    names = ('monitor.py', 'reliable_data.py', 'qqq_recovery.py', 'ndx_fallback.py', 'qqq_tail.py', 'price_checkpoint.py')
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode()); h.update(Path(__file__).with_name(name).read_bytes())
    return h.hexdigest()


def save(qqq, ndx, report, audit, now=None):
    dest = directory()
    if dest is None:
        return
    try:
        if dest.is_symlink():
            raise ValueError('Checkpoint directory cannot be a symlink')
        dest.mkdir(parents=True, exist_ok=True)
        stamp = utc(now)
        doc = {'version': VERSION, 'rules_sha256': _rules_hash(),
               'report_date': str(pd.Timestamp(report).date()), 'verified_at': stamp.isoformat(),
               'run_id': os.environ.get('GITHUB_RUN_ID'), 'commit_sha': os.environ.get('GITHUB_SHA'),
               'prices': {}, 'execution_authorized': False}
        for label, series in (('QQQ', qqq), ('NDX', ndx)):
            # Full input history retains the original EMA seed, unlike the 300-row
            # human-readable audit excerpt.
            data = series.to_csv(index_label='date', header=[label]).encode()
            digest = hashlib.sha256(data).hexdigest()
            path = dest / f'{label}.csv'
            if path.is_symlink():
                raise ValueError('Checkpoint file cannot be a symlink')
            temp = dest / f'{label}.tmp'
            if temp.is_symlink():
                raise ValueError('Checkpoint temporary file cannot be a symlink')
            temp.write_bytes(data); temp.replace(path)
            doc['prices'][label] = {'sha256': digest, 'rows': len(series),
                                    'source': series.attrs.get('source', 'unknown'),
                                    'index_name': series.index.name,
                                    'index_unit': series.index.unit}
        temp = dest / 'manifest.tmp'
        if temp.is_symlink() or (dest / 'manifest.json').is_symlink():
            raise ValueError('Checkpoint manifest cannot be a symlink')
        temp.write_text(json.dumps(doc, indent=2, allow_nan=False) + '\n')
        temp.replace(dest / 'manifest.json')
        audit.append({'request': 'checkpoint_save', 'status': 'staged', 'report_date': doc['report_date']})
    except (OSError, ValueError) as exc:
        audit.append({'request': 'checkpoint_save', 'status': 'unavailable', 'error': str(exc)})


def restore(report, audit, now=None):
    """Revalidate an earlier successful SAME-session snapshot; never update dates."""
    dest = directory()
    if dest is None:
        return None
    try:
        import reliable_data as data
        failures = ' '.join(str(item.get('error', '')).lower() for item in audit)
        if any(word in failures for word in ('conflict', 'mismatch', 'duplicate', 'wrong symbol', 'non-positive')):
            raise ValueError('Observed live price or identity conflict forbids checkpoint recovery')
        if dest.is_symlink():
            raise ValueError('Checkpoint directory cannot be a symlink')
        doc = json.loads(_read(dest / 'manifest.json'))
        report = pd.Timestamp(report)
        if doc.get('version') != VERSION or doc.get('rules_sha256') != _rules_hash():
            raise ValueError('Checkpoint contract does not match current code')
        if doc.get('report_date') != str(report.date()):
            raise ValueError('Checkpoint is not for the required report session')
        stamp, verified = utc(now), utc(doc['verified_at'])
        if not pd.Timedelta(0) <= stamp - verified <= pd.Timedelta(days=7):
            raise ValueError('Checkpoint verification time is stale or future')
        if verified < utc(monitor._calendar().session_close(report)) + pd.Timedelta(minutes=30):
            raise ValueError('Checkpoint was not verified after the report close')
        values = []
        for label in ('QQQ', 'NDX'):
            info = doc['prices'][label]
            body = _read(dest / f'{label}.csv')
            if hashlib.sha256(body).hexdigest() != info['sha256']:
                raise ValueError('Checkpoint SHA256 mismatch')
            frame = pd.read_csv(StringIO(body.decode()), float_precision='round_trip')
            if list(frame.columns) != ['date', label] or len(frame) != info['rows']:
                raise ValueError('Checkpoint schema or row count mismatch')
            index = pd.DatetimeIndex(pd.to_datetime(frame.date)).as_unit(info['index_unit'])
            index.name = info['index_name']
            series = pd.Series(frame[label].to_numpy(), index=index, name=label)
            if (series.index.has_duplicates or not series.index.is_monotonic_increasing
                    or not series.index.equals(series.index.normalize())
                    or series.index[-1] != report or series.isna().any()):
                raise ValueError('Checkpoint has invalid dates or missing values')
            series = data.validate_asof(series, report, label)
            series.attrs['source'] = (f'Same-session verified checkpoint from run {doc["run_id"]}, '
                                      f'verified {doc["verified_at"]}; original: {info["source"]}')
            values.append(series)
        monitor.validate_cross_source(*values, report)
        audit.append({'request': 'checkpoint_restore', 'status': 'ok',
                      'report_date': str(report.date()), 'original_verified_at': doc['verified_at'],
                      'original_run_id': doc['run_id'], 'new_market_observation': False})
        return tuple(values)
    except (OSError, ValueError, KeyError, TypeError, IndexError, monitor.MonitorError) as exc:
        audit.append({'request': 'checkpoint_restore', 'status': 'unavailable', 'error': str(exc)})
        return None
