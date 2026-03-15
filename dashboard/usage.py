"""
用量计算模块 - v3 (自然周/自然月版)
从各agent的.jsonl文件读取真实的message usage数据，聚合统计5h/week/month

修改 (v2 -> v3):
- 将滚动窗口(today/7d/30d)改为自然周/自然月
- 本周: 本周一00:00 -> 现在
- 本月: 每月1日00:00 -> 现在
- 添加请求限制信息 (limit字段)
"""
import json
import pathlib
import datetime
import time
import glob
import os

# OpenClaw 主目录
OCLAW_HOME = pathlib.Path.home() / '.openclaw'

# Agent列表
AGENT_IDS = ['main', 'creator', 'canmou', 'yunying', 'evolver', 'trader', 'community']

# 与TypeScript版本一致：只回溯62天内的数据
RUNTIME_USAGE_LOOKBACK_DAYS = 62
DAY_MS = 24 * 60 * 60 * 1000


def get_agent_sessions_dir(agent_id):
    """获取agent的sessions目录"""
    return OCLAW_HOME / 'agents' / agent_id / 'sessions'


def scan_jsonl_files(sessions_dir):
    """扫描sessions目录下的活跃.jsonl文件（排除deleted和reset），按TypeScript版本的方式过滤"""
    if not sessions_dir.exists():
        return []

    # 查找所有jsonl文件，排除.deleted.和.reset.
    pattern = str(sessions_dir / '*.jsonl*')
    files = glob.glob(pattern)

    # TypeScript版本：根据文件mtime进行lookback过滤
    # 计算62天前的毫秒时间戳
    now_ms = int(time.time() * 1000)
    lookback_ms = now_ms - (RUNTIME_USAGE_LOOKBACK_DAYS * DAY_MS)

    result = []
    for f in files:
        fname = pathlib.Path(f).name
        # 跳过已删除和重置的文件
        if '.deleted.' in fname or '.reset.' in fname:
            continue

        # 根据文件修改时间过滤（与TypeScript版本一致）
        # mtime is in seconds, convert to ms
        fpath = pathlib.Path(f)
        mtime_ms = int(fpath.stat().st_mtime * 1000)

        # 只有在62天内的文件才包含
        if mtime_ms >= lookback_ms:
            result.append(fpath)

    return result


def parse_usage_from_file(jsonl_path):
    """
    从jsonl文件中解析出usage数据
    返回: list of {timestamp: ms, input: int, output: int, total: int}
    """
    usages = []

    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # 只处理message类型，且role为assistant，且有usage数据的记录
                # 与TypeScript版本完全一致: if (asString(parsed.type) !== "message") continue; ...
                # if (asString(message.role) !== "assistant") continue;
                if data.get('type') == 'message':
                    msg = data.get('message', {})
                    if not msg:
                        continue

                    # 关键：只处理assistant角色的消息（与TypeScript版本一致）
                    if msg.get('role') != 'assistant':
                        continue

                    usage = msg.get('usage', {})
                    if not usage:
                        continue

                    # 获取timestamp (毫秒)
                    timestamp = data.get('timestamp')
                    if not timestamp:
                        # 尝试从message中获取
                        timestamp = msg.get('timestamp')

                    if timestamp:
                        # 解析时间戳 (可能是ISO字符串或毫秒数)
                        try:
                            if isinstance(timestamp, str):
                                # ISO格式: "2026-03-13T19:02:47.192Z"
                                ts_dt = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                ts_ms = int(ts_dt.timestamp() * 1000)
                            else:
                                ts_ms = timestamp
                        except (ValueError, OSError):
                            ts_ms = 0

                        # 获取token数量 (与TypeScript版本pickUsageTokens完全一致)
                        input_tokens = usage.get('input', 0) or 0
                        output_tokens = usage.get('output', 0) or 0
                        cache_read = usage.get('cacheRead', 0) or 0
                        cache_write = usage.get('cacheWrite', 0) or 0
                        # TypeScript: if (totalTokens > 0) return totalTokens; else return input+output+cacheRead+cacheWrite
                        total_tokens = usage.get('totalTokens', 0) or (input_tokens + output_tokens + cache_read + cache_write)

                        # 与TypeScript版本一致：不限制total_tokens>0，所有消息都计入requests计数
                        # 注意：events.length在TypeScript中直接使用，不检查tokens是否为0
                        usages.append({
                            'timestamp': ts_ms,
                            'input': input_tokens,
                            'output': output_tokens,
                            'cacheRead': cache_read,
                            'cacheWrite': cache_write,
                            'total': total_tokens
                        })

    except Exception as e:
        print(f"Error reading {jsonl_path}: {e}")

    return usages


def calculate_period_tokens(all_usages, period_days):
    """
    计算指定时间段内的token总量和请求次数
    使用本地时区计算（与3号API一致）

    Args:
        all_usages: 所有usage记录列表 (timestamp为UTC毫秒)
        period_days: 天数 (1=today, 7=7d, 30=30d)

    Returns:
        dict: {tokens, requestCount}
    """
    # 使用本地时区计算（与3号API一致）
    now_local = datetime.datetime.now()
    today = now_local.date()

    # 计算日期范围 (本地时间)
    start_date = today - datetime.timedelta(days=period_days - 1)
    end_date = today

    total_tokens = 0
    request_count = 0

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        # 将 UTC 毫秒时间戳转换为本地日期
        if ts_ms > 0:
            try:
                # 先转为UTC时间，再转换到本地时区
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()  # 转换到本地时区
                ts_date = ts_local.date()
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        # 判断是否在日期范围内
        if start_date <= ts_date <= end_date:
            total_tokens += usage.get('total', 0)
            request_count += 1

    return {
        'tokens': total_tokens,
        'requestCount': request_count
    }


def calculate_5h_tokens(all_usages):
    """
    计算过去5小时的token总量和请求次数

    Args:
        all_usages: 所有usage记录列表 (timestamp为UTC毫秒)

    Returns:
        dict: {tokens, requestCount}
    """
    # 当前本地时间往前5小时，需要使用带时区的datetime
    now_local = datetime.datetime.now().astimezone()
    cutoff_time = now_local - datetime.timedelta(hours=5)

    total_tokens = 0
    request_count = 0

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        if ts_ms > 0:
            try:
                # UTC毫秒时间戳转本地datetime
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()  # 转换到本地时区
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        # 判断是否在5小时范围内
        if ts_local >= cutoff_time:
            total_tokens += usage.get('total', 0)
            request_count += 1

    return {
        'tokens': total_tokens,
        'requestCount': request_count
    }


# 套餐限制常量
LIMIT_5H = 1200  # 1200次/小时 (lite套餐)
LIMIT_WEEK = None  # 本周无明确上限
LIMIT_MONTH = None  # 本月无明确上限


def get_today_start():
    """获取今天0点00分00秒的本地时间(带时区)"""
    now_local = datetime.datetime.now().astimezone()
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


def get_week_start():
    """获取本周一00:00:00的本地时间(带时区)"""
    now_local = datetime.datetime.now().astimezone()
    # weekday(): Monday=0, Sunday=6
    days_since_monday = now_local.weekday()
    week_start = now_local - datetime.timedelta(days=days_since_monday)
    return week_start.replace(hour=0, minute=0, second=0, microsecond=0)


def get_month_start():
    """获取本月1日00:00:00的本地时间(带时区)"""
    now_local = datetime.datetime.now().astimezone()
    return now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def calculate_daily_tokens(all_usages, year=None, month=None):
    """
    计算每日token用量（当月每日明细）
    使用本地时区

    Args:
        all_usages: 所有usage记录列表
        year: 年份，默认当前年
        month: 月份，默认当前月

    Returns:
        list: [{date: "2026-03-01", tokens: 123, requestCount: 10}, ...]
    """
    if year is None:
        now_local = datetime.datetime.now().astimezone()
        year = now_local.year
        month = now_local.month

    # 获取当月第一天
    month_start = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc).astimezone()
    # 获取当月最后一天（通过计算下月第一天减1天）
    if month == 12:
        next_month = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc).astimezone()
    else:
        next_month = datetime.datetime(year, month + 1, 1, tzinfo=datetime.timezone.utc).astimezone()
    month_end = next_month - datetime.timedelta(days=1)

    # 获取今天的本地日期（用于截止到今天）
    today_local = datetime.datetime.now().astimezone().date()

    # 按日期聚合
    daily_data = {}

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        if ts_ms > 0:
            try:
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()
                ts_date = ts_local.date()
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        # 只处理当月的数据
        if ts_date.year != year or ts_date.month != month:
            continue

        date_str = ts_date.strftime('%Y-%m-%d')

        if date_str not in daily_data:
            daily_data[date_str] = {'tokens': 0, 'requestCount': 0}

        daily_data[date_str]['tokens'] += usage.get('total', 0)
        daily_data[date_str]['requestCount'] += 1

    # 转换为列表格式，并按日期排序
    daily_list = []
    for date_str in sorted(daily_data.keys()):
        daily_list.append({
            'date': date_str,
            'tokens': daily_data[date_str]['tokens'],
            'requestCount': daily_data[date_str]['requestCount']
        })

    return daily_list


def calculate_today_tokens(all_usages):
    """
    计算今日(今天0点至今)的token总量和请求次数
    使用本地时区，日期边界

    Args:
        all_usages: 所有usage记录列表

    Returns:
        dict: {tokens, requestCount}
    """
    today_start = get_today_start()
    total_tokens = 0
    request_count = 0

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        if ts_ms > 0:
            try:
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        if ts_local >= today_start:
            total_tokens += usage.get('total', 0)
            request_count += 1

    return {
        'tokens': total_tokens,
        'requestCount': request_count
    }


def calculate_week_tokens(all_usages):
    """
    计算本周(自然周，周一至今)的token总量和请求次数

    Args:
        all_usages: 所有usage记录列表

    Returns:
        dict: {tokens, requestCount}
    """
    week_start = get_week_start()
    total_tokens = 0
    request_count = 0

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        if ts_ms > 0:
            try:
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        if ts_local >= week_start:
            total_tokens += usage.get('total', 0)
            request_count += 1

    return {
        'tokens': total_tokens,
        'requestCount': request_count
    }


def calculate_month_tokens(all_usages):
    """
    计算本月(自然月，1日至今)的token总量和请求次数

    Args:
        all_usages: 所有usage记录列表

    Returns:
        dict: {tokens, requestCount}
    """
    month_start = get_month_start()
    total_tokens = 0
    request_count = 0

    for usage in all_usages:
        ts_ms = usage.get('timestamp', 0)

        if ts_ms > 0:
            try:
                ts_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
                ts_local = ts_utc.astimezone()
            except (ValueError, OSError, TypeError):
                continue
        else:
            continue

        if ts_local >= month_start:
            total_tokens += usage.get('total', 0)
            request_count += 1

    return {
        'tokens': total_tokens,
        'requestCount': request_count
    }


def get_usage_cost():
    """
    获取各时间段的token用量统计

    Returns:
        dict: 包含periods的数据 (5h/week/month)
    """
    # 收集所有agent的所有usage数据
    all_usages = []

    for agent_id in AGENT_IDS:
        sessions_dir = get_agent_sessions_dir(agent_id)
        jsonl_files = scan_jsonl_files(sessions_dir)

        for jsonl_path in jsonl_files:
            usages = parse_usage_from_file(jsonl_path)
            all_usages.extend(usages)

    if not all_usages:
        return {
            'periods': [
                {'key': '5h', 'tokens': 0, 'requestCount': 0, 'limit': LIMIT_5H},
                {'key': 'week', 'tokens': 0, 'requestCount': 0, 'limit': LIMIT_WEEK},
                {'key': 'month', 'tokens': 0, 'requestCount': 0, 'limit': LIMIT_MONTH},
            ],
            'subscription': {
                'status': 'not_connected',
                'planLabel': '自主计算'
            }
        }

    # 计算各时间段的用量
    hours_5_data = calculate_5h_tokens(all_usages)
    today_data = calculate_today_tokens(all_usages)
    week_data = calculate_week_tokens(all_usages)
    month_data = calculate_month_tokens(all_usages)
    
    # 计算每日用量（当前月份）
    daily_data = calculate_daily_tokens(all_usages)

    return {
        'periods': [
            {'key': '5h', 'tokens': hours_5_data['tokens'], 'requestCount': hours_5_data['requestCount'], 'limit': LIMIT_5H},
            {'key': 'today', 'tokens': today_data['tokens'], 'requestCount': today_data['requestCount']},
            {'key': 'week', 'tokens': week_data['tokens'], 'requestCount': week_data['requestCount'], 'limit': LIMIT_WEEK},
            {'key': 'month', 'tokens': month_data['tokens'], 'requestCount': month_data['requestCount'], 'limit': LIMIT_MONTH},
        ],
        'subscription': {
            'status': 'not_connected',
            'planLabel': '自主计算'
        },
        'daily': daily_data
    }


if __name__ == '__main__':
    # 测试用
    result = get_usage_cost()
    print(json.dumps(result, ensure_ascii=False, indent=2))