"""Idempotent issue notifications; state is read from bot-authored comments."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

HEARTBEAT = '<!-- qld-tqqq-automation-heartbeat -->'


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Missing timestamp timezone')
    return result.astimezone(timezone.utc)


def plain(value):
    return str(value).replace('|', '/').replace('\n', ' ').replace('\r', ' ').replace('`', "'")


def trusted(comment):
    return comment.get('user', {}).get('login') == 'github-actions[bot]'


def signal_marker(day):
    return f'<!-- qld-tqqq-signal:{day}:success -->'


def publication_plan(status, comments, now=None):
    now = now or now_utc()
    if status.get('mode') == 'replay':
        return 'replay'
    if status.get('status') != 'ok':
        return 'data_error'
    timing = status.get('month_end', {})
    marker = signal_marker(timing.get('signal_date', 'unknown'))
    # A read failure must raise upstream, not be interpreted as no existing signal.
    if any(trusted(c) and c.get('body', '').startswith(marker) for c in comments):
        return 'already_published'
    cutoff = parse_time(timing['publish_deadline'])
    if now >= cutoff:
        return 'missed_deadline'
    if (status.get('publish_notification') is True
            and status.get('report_date') == timing.get('signal_date')):
        return 'publish'
    return 'monitor_only'


class IssueClient:
    def __init__(self, repo, issue='1'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Invalid repository')
        self.repo, self.issue = repo, int(issue)

    def api(self, path, method='GET', body=None, paged=False):
        args = ['gh', 'api', '--method', method, path]
        if paged:
            args.extend(['--paginate', '--slurp'])
        if body is not None:
            args.extend(['--input', '-'])
        run = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                             text=True, capture_output=True, timeout=90)
        if run.returncode:
            raise RuntimeError(f'GitHub {method} failed: {run.stderr[:500]}')
        return json.loads(run.stdout) if run.stdout.strip() else None

    def comments(self):
        pages = self.api(f'repos/{self.repo}/issues/{self.issue}/comments?per_page=100', paged=True)
        return [item for page in pages for item in page]

    def upsert(self, marker, body, comments):
        previous = next((c for c in comments if trusted(c) and c.get('body', '').startswith(marker)), None)
        if previous:
            return self.api(f'repos/{self.repo}/issues/comments/{previous["id"]}', 'PATCH', {'body': body})
        result = self.api(f'repos/{self.repo}/issues/{self.issue}/comments', 'POST', {'body': body})
        comments.append(result)
        return result


def repair_errors(client, status, comments, run_url):
    report = status.get('report_date', '')
    floor = (datetime.fromisoformat(report) - timedelta(days=31)).date().isoformat()
    for c in comments:
        if not trusted(c):
            continue
        body = c.get('body', '')
        match = re.match(r'<!-- qld-tqqq-(?:signal:(\d{4}-\d\d-\d\d):error|data-status:(\d{4}-\d\d-\d\d)) -->', body)
        if not match or '<!-- resolved -->' in body:
            continue
        day = match.group(1) or match.group(2)
        if floor <= day <= report:
            resolved = (match.group(0) + '\n<!-- resolved -->\n## 数据问题已恢复\n\n'
                        f'已在 {status["completed_at"]} 验证 QQQ 和 NDX 收盘数据至 {report}。'
                        '\n原失败未产生交易指令；不补做已过期交易。\n\n'
                        f'[恢复核验运行]({run_url})\n\n<details><summary>保留原始诊断</summary>\n\n'
                        + body.replace('<!--', '&lt;!--').replace('-->', '--&gt;') + '\n</details>\n')
            client.api(f'repos/{client.repo}/issues/comments/{c["id"]}', 'PATCH', {'body': resolved})


def publish(output_dir: Path, client, env=None, now=None) -> int:
    env, now = env or os.environ, now or now_utc()
    path = output_dir / 'status.json'
    status = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'status': 'error', 'mode': 'live', 'report_date': 'unknown',
        'error': 'Signal generation did not complete; inspect dependency/test/job failure',
        'completed_at': now.isoformat()}
    if status.get('mode') == 'replay':
        print('Historical replay: no issue mutation or live heartbeat update')
        return 0
    comments = client.comments()
    plan = publication_plan(status, comments, now)
    run_url = f'https://github.com/{client.repo}/actions/runs/{env.get("GITHUB_RUN_ID", "unknown")}'
    if plan == 'publish':
        # Recheck the deadline immediately before sending, not just at generation.
        fresh_plan = publication_plan(status, comments)
        if fresh_plan != 'publish':
            plan = fresh_plan
        else:
            signal = status['month_end']['signal_date']
            client.upsert(signal_marker(signal), (output_dir / 'month_end.md').read_text(encoding='utf-8'), comments)
            plan = 'published'
    if plan == 'data_error':
        day = status.get('report_date', 'unknown')
        marker = f'<!-- qld-tqqq-data-status:{day} -->'
        body = (f'{marker}\n## 收盘数据尚未通过校验\n\n'
                f'报告日 {plain(day)}；本次不产生交易指令。\n\n'
                f'原因：{plain(status.get("error", "unknown"))}\n\n'
                f'[本次运行]({run_url})\n\n后续成功会自动将此记录标记为已恢复，保留原始诊断。')
        client.upsert(marker, body, comments)
    elif status.get('status') == 'ok':
        repair_errors(client, status, comments, run_url)
    if plan == 'missed_deadline':
        day = status['month_end']['signal_date']
        marker = f'<!-- qld-tqqq-missed-deadline:{day} -->'
        client.upsert(marker, f'{marker}\n## 月末通知错过执行窗口，需要人工检查\n\n'
                      f'信号日 {day} 的通知未能确认已发布；截止时间 '
                      f'{status["month_end"]["publish_deadline"]} 已过。'
                      '\n**禁止根据过期开盘价补单。该程序未下单。**\n\n'
                      f'[本次运行]({run_url})', comments)
    health = ('数据核验成功（不代表调度准时）' if plan not in ('data_error', 'missed_deadline')
              else '需要检查：数据失败或月末执行窗口已错过')
    rows = [
        ('检查完成（PT）', now.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M:%S %Z')),
        ('本次触发', env.get('GITHUB_EVENT_NAME', 'unknown')),
        ('配置 cron（UTC）', env.get('SCHEDULE_EXPRESSION', '') or 'push/manual'),
        ('报告交易日', status.get('report_date', 'unknown')),
        ('按交易日历应有数据至', status.get('expected_session', 'unknown')),
        ('数据核验', status.get('status', 'error')),
        ('月末通知处理', plan),
        ('最新月末信号日', status.get('month_end', {}).get('signal_date', 'unknown')),
        ('该信号发布截止UTC', status.get('month_end', {}).get('publish_deadline', 'unknown')),
        ('自动交易', '未接券商，无自动下单'),
    ]
    for symbol, source in status.get('sources', {}).items():
        rows.append((f'{symbol} 已核验收盘', f'{source["latest_date"]} / {source["close"]:.4f}'))
        rows.append((f'{symbol} 来源', source['source']))
    rows.append(('本次数据尝试次数', sum('started_at' in x for x in status.get('attempts', []))))
    body = (f'{HEARTBEAT}\n## 自动运行状态\n\n**{health}**\n\n'
            '| 项目 | 最近一次检查 |\n|---|---|\n' + '\n'.join(
                f'| {plain(k)} | {plain(v)} |' for k, v in rows) +
            f'\n\n[查看本次运行]({run_url})\n\n'
            '这是带时间戳的最近一次结果，不是实时保证。GitHub cron可能延迟或丢失；'
            '凌晨补跑使用最近已完成的真实交易日，绝不拿旧价格冒充新收盘。'
            '非月末只更新这条状态，不新增交易信号。\n')
    client.upsert(HEARTBEAT, body, comments)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'publication.json').write_text(json.dumps({'plan': plan, 'at': now.isoformat()}, indent=2), encoding='utf-8')
    return 2 if plan in ('data_error', 'missed_deadline') else 0


def main():
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Issue publication is restricted to main')
    client = IssueClient(os.environ['GITHUB_REPOSITORY'])
    return publish(Path('signal_output'), client)


if __name__ == '__main__':
    raise SystemExit(main())
