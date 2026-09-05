"""Calendar-aware operation around the unchanged monthly strategy calculator."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

import monitor
import exact_monitor
from reliable_data import load_prices


def utc(value=None) -> pd.Timestamp:
    result = pd.Timestamp(value if value is not None else datetime.now(timezone.utc))
    if result.tzinfo is None:
        raise ValueError('An explicit UTC offset is required')
    return result.tz_convert('UTC')


def latest_session(now=None) -> pd.Timestamp:
    now = utc(now)
    cal = monitor._calendar()
    today = now.tz_convert('America/New_York').tz_localize(None).normalize()
    sessions = monitor._normalize_sessions(cal.sessions_in_range(today - pd.Timedelta(days=21), today))
    for session in reversed(sessions):
        if utc(cal.session_close(session)) + pd.Timedelta(minutes=30) <= now:
            return session
    raise monitor.MonitorError('No completed session in calendar window')


def monthly_timing(report, now, replay=False) -> dict:
    report, now = pd.Timestamp(report), utc(now)
    cal = monitor._calendar()
    monthly = monitor._month_end_sessions(report - pd.Timedelta(days=90), report)
    if len(monthly) == 0:
        raise monitor.MonitorError('No completed month-end')
    signal = monthly[-1]
    execution = monitor._next_session(signal)
    open_at = utc(cal.session_open(execution))
    cutoff = open_at - pd.Timedelta(minutes=10)
    return {'signal_date': str(signal.date()), 'execution_date': str(execution.date()),
            'open_at': open_at.isoformat(), 'publish_deadline': cutoff.isoformat(),
            'publish_notification': bool(not replay and report == signal and now < cutoff),
            'expired': bool(now >= cutoff)}


def atomic_json(path: Path, obj: dict) -> None:
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def generate(output_dir: Path, report_date=None, now=None, loader=load_prices) -> int:
    started = utc(now)
    output_dir.mkdir(parents=True, exist_ok=True)
    status = {'status': 'error', 'started_at': started.isoformat(),
              'mode': 'replay' if report_date else 'live', 'attempts': [],
              'publish_notification': False, 'intramonth_rebalance': False}
    try:
        expected = latest_session(started)
        report = pd.Timestamp(report_date) if report_date else expected
        if report.tzinfo is not None or report != report.normalize():
            raise monitor.MonitorError('Report date must be a date without time or timezone')
        status['report_date'] = str(report.date())
        status['expected_session'] = str(expected.date())
        if report > expected:
            raise monitor.MonitorError('Requested close is not complete; no intraday/future data allowed')
        if not monitor._calendar().is_session(report):
            raise monitor.MonitorError('Requested date is not an exchange session')
        # Save timing even when price retrieval fails so a deadline breach is visible.
        status['month_end'] = monthly_timing(report, started, bool(report_date))
        qqq, ndx = loader(report, status['attempts'])
        decision = monitor.build_decision(qqq, ndx, report)
        candidate = monitor.build_decision(qqq, ndx, status['month_end']['signal_date'])
        finished = utc(now) if now is not None else utc()
        timing = monthly_timing(report, finished, bool(report_date))
        prices = pd.concat([qqq.rename('QQQ'), ndx.rename('NDX')], axis=1).loc[:report].tail(300)
        prices.to_csv(output_dir / 'verified_closes.csv', index_label='date')
        status.update({'status': 'ok', 'completed_at': finished.isoformat(),
                       'month_end': timing, 'publish_notification': timing['publish_notification'],
                       'market_status': 'open', 'is_month_end': decision.is_month_end,
                       'sources': {k: {'source': s.attrs.get('source', 'injected-test-data'),
                                      'latest_date': str(s.index[-1].date()),
                                      'close': float(s.iloc[-1]), 'rows': len(s)}
                                   for k, s in [('QQQ', qqq), ('NDX', ndx)]},
                       'data_sha256': hashlib.sha256((output_dir / 'verified_closes.csv').read_bytes()).hexdigest()})
        payload = exact_monitor.exact_payload(decision)
        payload.update({'publish_notification': timing['publish_notification'],
                        'mode': status['mode'], 'sources': status['sources'],
                        'calendar_poll_frequency': 'hourly_overnight',
                        'verified_at': status['completed_at']})
        if not timing['publish_notification']:
            for item in payload['decisions']:
                item['instruction'] = 'historical_only' if report_date else 'no_rebalance'
        # A historical/month-end audit must never resemble a fresh buy instruction.
        if decision.is_month_end and not timing['publish_notification']:
            report_text = ('## 月末历史目标核验（不可作为新交易指令）\n\n'
                           f'信号日 {report.date()}；原执行日 {timing["execution_date"]}。\n'
                           '历史复算或开盘截止时间已过；禁止按历史价格补单。\n\n'
                           '| 方案 | 历史ETF目标 |\n|---|---:|\n' + '\n'.join(
                               f'| {x["symbol"]} | {x["target_weight"]:.2%} |' for x in payload['decisions']))
        else:
            report_text = exact_monitor.render_exact_markdown(decision)
        if report_date:
            report_text = '历史复算模式；不发布正式信号、不更新生产心跳。\n\n' + report_text
        report_text += '\n\n### 数据来源与截止日\n' + '\n'.join(
            f'- {k}: {v["latest_date"]}, close={v["close"]:.4f}, rows={v["rows"]}, {v["source"]}'
            for k, v in status['sources'].items()) + '\n'
        atomic_json(output_dir / 'signal.json', payload)
        candidate_payload = exact_monitor.exact_payload(candidate)
        candidate_payload['publish_notification'] = timing['publish_notification']
        candidate_payload['calendar_poll_frequency'] = 'hourly_overnight'
        if not timing['publish_notification']:
            for item in candidate_payload['decisions']:
                item['instruction'] = 'historical_only'
        atomic_json(output_dir / 'month_end.json', candidate_payload)
        candidate_text = exact_monitor.render_exact_markdown(candidate) if timing['publish_notification'] else (
            '# 月末目标历史归档，禁止作为新交易指令\n\n'
            + json.dumps(candidate_payload['decisions'], ensure_ascii=False, indent=2))
        (output_dir / 'month_end.md').write_text(candidate_text + '\n', encoding='utf-8')
        (output_dir / 'report.md').write_text(report_text, encoding='utf-8')
        atomic_json(output_dir / 'status.json', status)
        print(report_text)
        return 0
    except Exception as exc:
        status['status'] = 'error'
        status['publish_notification'] = False
        status['error'] = str(exc)
        status['completed_at'] = (utc(now) if now is not None else utc()).isoformat()
        monitor.write_error(status.get('report_date', 'unknown'), exc, output_dir)
        atomic_json(output_dir / 'status.json', status)
        print(str(exc), file=sys.stderr)
        return 2


def main(argv=None):
    args = monitor.parse_args(argv)
    return generate(Path(args.output_dir), args.report_date)


if __name__ == '__main__':
    raise SystemExit(main())
