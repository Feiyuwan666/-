#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端盯盘脚本（跑在 GitHub Actions 里，不依赖手机后台）

读 data/positions.json → 拉腾讯财经行情 → 判断达标/止损 → Server酱推微信 → 写 data/state.json
"""
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_FILE = os.path.join(ROOT, 'data', 'positions.json')
STATE_FILE = os.path.join(ROOT, 'data', 'state.json')


def log(msg):
    print(msg, flush=True)


def beijing_now():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=8)).replace(tzinfo=None)


def in_session(now):
    if now.weekday() >= 5:
        return False, '周末休市'
    m = now.hour * 60 + now.minute
    if 9 * 60 + 30 <= m <= 11 * 60 + 30:
        return True, ''
    if 13 * 60 <= m <= 15 * 60:
        return True, ''
    return False, '非交易时段'


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')


def fetch_quotes(codes):
    """返回 {code: {name, price, preClose, pct, volume}}"""
    if not codes:
        return {}
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    req = urllib.request.Request(url, headers={
        'Referer': 'https://gu.qq.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    })
    raw = urllib.request.urlopen(req, timeout=20).read()
    text = raw.decode('gbk', 'replace')
    out = {}
    for c in codes:
        m = re.search(r'v_' + c + r'="([^"]*)"', text)
        if not m:
            continue
        f = m.group(1).split('~')
        if len(f) <= 32:
            continue
        try:
            price = float(f[3])
        except ValueError:
            continue
        if price <= 0:
            continue
        out[c] = {
            'name': f[1].strip() or c.upper(),
            'price': price,
            'preClose': float(f[4] or 0),
            'pct': float(f[32] or 0),
            'volume': float(f[6] or 0),
        }
    return out


def push_serverchan(key, title, desp):
    body = urllib.parse.urlencode({'title': title, 'desp': desp}).encode()
    req = urllib.request.Request(
        'https://sctapi.ftqq.com/%s.send' % key,
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    resp = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')
    try:
        return json.loads(resp).get('code') == 0, resp[:200]
    except Exception:
        return False, resp[:200]


def set_output(name, value):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('%s=%s\n' % (name, value))


def main():
    now = beijing_now()
    stamp = now.strftime('%Y-%m-%d %H:%M')
    force = os.environ.get('FORCE', '').lower() in ('1', 'true', 'yes')

    data = load_json(POS_FILE, {})
    positions = data.get('positions') or []
    settings = data.get('settings') or {}
    target = float(settings.get('target') or 0)
    stop = float(settings.get('stop') or 0)

    log('== 云端盯盘 %s（北京时间）==' % stamp)
    if not positions:
        log('data/positions.json 里还没有持仓，跳过。')
        set_output('commit', 'false')
        return 0

    ok, reason = in_session(now)
    if not ok and not force:
        log('不在交易时段（%s），跳过。持仓 %d 只。' % (reason, len(positions)))
        set_output('commit', 'false')
        return 0

    codes = []
    for p in positions:
        c = str(p.get('code') or '').strip().lower()
        if c and c not in codes:
            codes.append(c)

    try:
        quotes = fetch_quotes(codes)
    except Exception as e:
        log('行情请求失败：%s' % e)
        set_output('commit', 'false')
        return 1

    if not quotes:
        log('没拿到任何行情（接口异常或代码不对）。')
        set_output('commit', 'false')
        return 1

    if all(q['volume'] <= 0 for q in quotes.values()) and not force:
        log('全部标的成交量为 0，判定休市，跳过。')
        set_output('commit', 'false')
        return 0

    state = load_json(STATE_FILE, {})
    alerts = state.get('alerts') or {}
    today = now.strftime('%Y-%m-%d')
    next_alerts = dict(alerts)
    fired = []
    key = os.environ.get('SERVERCHAN_KEY', '').strip()

    for p in positions:
        code = str(p.get('code') or '').strip().lower()
        q = quotes.get(code)
        if not q:
            log('  %s 无行情' % code)
            continue
        try:
            buy = float(p.get('buyPrice') or 0)
            shares = float(p.get('shares') or 0)
        except (TypeError, ValueError):
            continue
        if buy <= 0:
            continue
        status = (p.get('status') or 'holding').strip()
        profit = (q['price'] - buy) * shares
        pct = (q['price'] - buy) / buy * 100
        log('  %s %s 现价 %.2f 盈亏 %+.2f 元（%+.2f%%）状态=%s'
            % (code, q['name'], q['price'], profit, pct, status))

        if status != 'holding':
            continue

        level = None
        if target > 0 and profit >= target:
            level = 'target'
        elif stop > 0 and pct <= -stop:
            level = 'stopped'
        if not level:
            continue

        prev = alerts.get(code) or {}
        if prev.get('level') == level and prev.get('date') == today:
            log('    → 今天已经推过 %s，跳过重复提醒' % level)
            continue

        if level == 'target':
            title = '🎯 达标信号：%s（%s）' % (q['name'], code.upper())
            desp = ('现价：%.2f 元\n买入价：%.2f 元\n数量：%d 股\n'
                    '盈利：%+.2f 元（%+.2f%%）\n时间：%s\n\n已到目标盈利 %+.2f 元，可以考虑卖出～'
                    % (q['price'], buy, shares, profit, pct, stamp, target))
        else:
            title = '🛑 止损信号：%s（%s）' % (q['name'], code.upper())
            desp = ('现价：%.2f 元\n买入价：%.2f 元\n数量：%d 股\n'
                    '亏损：%+.2f 元（%+.2f%%）\n时间：%s\n\n已跌破止损线 -%.2f%%，注意控制风险'
                    % (q['price'], buy, shares, profit, pct, stamp, stop))

        if not key:
            log('    ⚠️ 未配置 SERVERCHAN_KEY，只记录不推送')
        else:
            try:
                sent, info = push_serverchan(key, title, desp)
                log('    → 推送%s %s' % ('成功' if sent else '失败', '' if sent else info))
            except Exception as e:
                log('    → 推送异常：%s' % e)

        next_alerts[code] = {'level': level, 'date': today, 'at': stamp,
                             'price': q['price'], 'profit': round(profit, 2)}
        fired.append('%s %s' % (code, '达标' if level == 'target' else '止损'))

    changed = json.dumps(next_alerts, ensure_ascii=False, sort_keys=True) != \
        json.dumps(alerts, ensure_ascii=False, sort_keys=True)

    summary = ('；'.join(fired)) if fired else ('无新信号（本次检查 %d 只）' % len(quotes))
    save_json(STATE_FILE, {
        'checked_at': stamp,
        'summary': summary,
        'alerts': next_alerts,
    })

    log('== 结果：%s ==' % summary)

    # 只在“有信号变化”或每 30 分钟时才提交 state，避免仓库被刷屏
    every_half = now.minute in (0, 1, 2, 30, 31, 32)
    set_output('commit', 'true' if (changed or every_half) else 'false')
    return 0


if __name__ == '__main__':
    sys.exit(main())
