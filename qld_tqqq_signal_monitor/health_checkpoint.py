"""Carry last verified-session evidence through later failed fetches, for display.

This checkpoint NEVER supplies prices, weights, or authorization to the strategy.
The latest attempt remains failed when its data checks fail.
"""
from datetime import date, datetime, timezone, timedelta
import json
import re

MARKER = '<!-- qld-tqqq-last-verified:'
HEARTBEAT = '<!-- qld-tqqq-automation-heartbeat -->'


def validate(value, now):
    try:
        if not isinstance(value,dict) or value.get('version') != 1:
            return None
        report = value['report_date']
        if date.fromisoformat(report).isoformat() != report:
            return None
        verified = datetime.fromisoformat(value['verified_at'].replace('Z','+00:00'))
        if verified.tzinfo is None or verified > now+timedelta(seconds=30):
            return None
        if date.fromisoformat(report) > verified.date():
            return None
        if not re.fullmatch(r'[0-9]+', str(value['run_id'])):
            return None
        digest = value.get('data_sha256')
        if digest is not None and not re.fullmatch(r'[a-f0-9]{64}',digest):
            return None
        return {'version':1,'report_date':report,'verified_at':verified.astimezone(timezone.utc).isoformat(),
                'run_id':str(value['run_id']),'data_sha256':digest}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def choose(status, comments, env, now):
    candidates = []
    for comment in comments:
        body = comment.get('body','')
        if comment.get('user',{}).get('login') != 'github-actions[bot]' or not body.startswith(HEARTBEAT):
            continue
        for line in body.splitlines():
            if line.startswith(MARKER) and line.endswith(' -->'):
                try:
                    raw = json.loads(line[len(MARKER):-4])
                except (ValueError,TypeError):
                    continue
                candidate = validate(raw, now)
                if candidate: candidates.append(candidate)
    if status.get('status') == 'ok' and status.get('mode') == 'live':
        if status.get('expected_session',status.get('report_date')) == status.get('report_date'):
            candidate = validate({'version':1, 'report_date':status.get('report_date'),
                'verified_at':status.get('completed_at'), 'run_id':env.get('GITHUB_RUN_ID'),
                'data_sha256':status.get('data_sha256')}, now)
            if candidate: candidates.append(candidate)
    return max(candidates,key=lambda x:(x['report_date'],x['verified_at'])) if candidates else None


def encode(checkpoint):
    return MARKER + json.dumps(checkpoint,sort_keys=True,separators=(',',':')) + ' -->'
