#!/usr/bin/env python3
"""
三省六部 · 看板本地 API 服务器
Port: 7891 (可通过 --port 修改)

Endpoints:
  GET  /                       → dashboard.html
  GET  /api/live-status        → data/live_status.json
  GET  /api/agent-config       → data/agent_config.json
  POST /api/set-model          → {agentId, model}
  GET  /api/model-change-log   → data/model_change_log.json
  GET  /api/last-result        → data/last_model_change_result.json
"""
import json, pathlib, subprocess, sys, threading, argparse, datetime, logging, re, os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, unquote
from urllib.request import Request, urlopen

# 引入文件锁工具，确保与其他脚本并发安全
scripts_dir = str(pathlib.Path(__file__).parent / 'scripts')
sys.path.insert(0, scripts_dir)
from file_lock import atomic_json_read, atomic_json_write, atomic_json_update
from utils import validate_url

# 导入三省六部任务系统（完全版）
try:
    import asyncio
    from task_state import (
        TaskService, EventBus, TaskState, 
        TOPIC_TASK_CREATED, TOPIC_TASK_DISPATCH, 
        TOPIC_TASK_STATUS, TOPIC_TASK_COMPLETED,
        SIX_DEPARTMENTS
    )
    _task_service = None
    _task_event_bus = None
    
    def _init_task_service():
        global _task_service, _task_event_bus
        if _task_service is None:
            _task_event_bus = EventBus()
            _task_service = TaskService(_task_event_bus)
            
            # 订阅任务事件，写入task_events.json
            def on_task_event(event):
                topic = event.get("topic", "")
                payload = event.get("payload", {})
                task_id = payload.get("task_id", "")
                title = payload.get("title", "")
                task_state = payload.get("state", payload.get("to", ""))
                from_state = payload.get("from", "")
                to_state = payload.get("to", "")
                org = payload.get("org", "")
                
                # 读取现有事件
                task_file = pathlib.Path(__file__).parent / 'task_events.json'
                try:
                    events = json.loads(task_file.read_text(encoding='utf-8'))
                except:
                    events = []
                
                # 根据事件类型生成描述（完全版）
                if topic == "task.created":
                    desc = f"新任务: {title}"
                    event_type = "info"
                elif topic == "task.dispatch":
                    if org:
                        desc = f"{task_id} 派发到 {org}"
                    else:
                        desc = f"{task_id}: {from_state} → {to_state}"
                    event_type = "info"
                elif topic == "task.status":
                    desc = f"{task_id}: {from_state} → {to_state}"
                    event_type = "info"
                elif topic == "task.completed":
                    desc = f"{task_id} 已完成"
                    event_type = "success"
                else:
                    return
                
                # 添加事件
                now = datetime.datetime.now()
                
                # 中文标题映射
                title_map = {
                    "task.created": "任务创建",
                    "task.status": "任务状态",
                    "task.completed": "任务完成",
                    "task.dispatch": "任务派发",
                }
                event_title = title_map.get(topic, topic.replace("task.", "任务"))
                
                events.insert(0, {
                    "time": now.strftime("%H:%M"),
                    "sort_key": now.isoformat(),
                    "title": event_title,
                    "desc": desc,
                    "type": event_type
                })
                
                # 只保留最近30条
                events = events[:30]
                
                # 写回文件
                task_file.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
            
            # 订阅所有任务事件
            _task_event_bus.subscribe("*", on_task_event)
            print("[三省六部完全版] 任务系统已初始化")
    _init_task_service()
except Exception as e:
    print(f"[三省六部] 任务系统加载失败: {e}")
    _task_service = None
    _task_event_bus = None

log = logging.getLogger('server')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

# ========== 任务状态监控 ==========
# 用于跟踪上次检查的任务状态快照 (task_id -> {state, org})
_task_state_snapshot = {}

def _get_task_event_file():
    """获取 task_events.json 文件路径"""
    return pathlib.Path(__file__).parent / 'task_events.json'

def _add_task_event(topic, task_id, title, desc, event_type="info"):
    """添加任务事件到 task_events.json（去重检查）"""
    task_file = _get_task_event_file()
    try:
        events = json.loads(task_file.read_text(encoding='utf-8'))
    except:
        events = []
    
    now = datetime.datetime.now()
    timestamp = now.isoformat()
    
    # 检查是否已存在相同 task_id 和时间的事件（避免重复写入）
    existing = any(
        e.get('desc', '').startswith(desc.split(':')[0] if ':' in desc else desc[:20]) 
        and e.get('sort_key', '').startswith(timestamp[:19])
        for e in events[:5]  # 只检查最近5条
    )
    if existing:
        return
    
    # 中文标题映射
    title_map = {
        "task.created": "任务创建",
        "task.status": "任务状态",
        "task.completed": "任务完成",
        "task.dispatch": "任务派发",
    }
    event_title = title_map.get(topic, topic.replace("task.", "任务"))
    
    events.insert(0, {
        "time": now.strftime("%H:%M"),
        "sort_key": timestamp,
        "title": event_title,
        "desc": desc,
        "type": event_type
    })
    
    # 只保留最近30条
    events = events[:30]
    task_file.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f"[任务监控] 事件记录: {topic} - {desc}")

def _check_task_changes():
    """定时检查任务状态变化并记录事件"""
    global _task_state_snapshot
    
    try:
        tasks = load_tasks()
        if not tasks:
            return
        
        current_tasks = {}  # task_id -> {state, org}
        
        for task in tasks:
            task_id = task.get('id', '')
            if not task_id:
                continue
            
            state = task.get('state', '')
            org = task.get('org', task.get('assigned_org', ''))
            current_tasks[task_id] = {'state': state, 'org': org}
            
            # 对比上次状态
            if task_id not in _task_state_snapshot:
                # 新任务出现 -> task.created
                title = task.get('title', task_id)
                _add_task_event("task.created", task_id, title, f"新任务: {title}", "info")
            else:
                prev = _task_state_snapshot[task_id]
                prev_state = prev.get('state', '')
                prev_org = prev.get('org', '')
                
                # 任务派发 - org 发生变化
                if org and org != prev_org:
                    title = task.get('title', task_id)
                    _add_task_event("task.dispatch", task_id, title, f"{task_id} 派发到 {org}", "info")
                
                # 任务完成 - 状态变为 Done/Cancelled
                if state in ('Done', 'Cancelled', 'Completed', 'canceled', 'completed') and prev_state != state:
                    title = task.get('title', task_id)
                    _add_task_event("task.completed", task_id, title, f"{task_id} 已完成", "success")
        
        # 更新快照
        _task_state_snapshot = current_tasks
        
    except Exception as e:
        log.warning(f"[任务监控] 检查失败: {e}")

def _start_task_monitor():
    """启动任务状态监控定时任务"""
    # 立即运行一次（等待服务完全启动后）
    def run_first():
        import time
        time.sleep(5)  # 等待5秒让其他初始化完成
        _check_task_changes()
    
    threading.Thread(target=run_first, daemon=True).start()
    
    # 每30秒运行一次
    def monitor_loop():
        while True:
            import time
            time.sleep(30)
            _check_task_changes()
    
    threading.Thread(target=monitor_loop, daemon=True).start()
    print("[任务监控] 定时任务已启动 (每30秒检查一次)")

OCLAW_HOME = pathlib.Path.home() / '.openclaw'
WORKSPACE_DIR = OCLAW_HOME / 'workspace'
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MB
ALLOWED_ORIGIN = None  # Set via --cors; None means restrict to localhost
_DEFAULT_ORIGINS = {
    'http://127.0.0.1:7891', 'http://localhost:7891',
    'http://127.0.0.1:5173', 'http://localhost:5173',  # Vite dev server
}
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')

BASE = pathlib.Path(__file__).parent
DIST = BASE / 'dist'          # React 构建产物 (npm run build)
DATA = BASE.parent / "data"
SCRIPTS = BASE.parent / 'scripts'

# 静态资源 MIME 类型
_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':  'font/ttf',
    '.map':  'application/json',
}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def cors_headers(h):
    req_origin = h.headers.get('Origin', '')
    if ALLOWED_ORIGIN:
        origin = ALLOWED_ORIGIN
    elif req_origin in _DEFAULT_ORIGINS:
        origin = req_origin
    else:
        origin = 'http://127.0.0.1:7891'
    h.send_header('Access-Control-Allow-Origin', origin)
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type')


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def load_tasks():
    return atomic_json_read(DATA / 'tasks_source.json', [])


def save_tasks(tasks):
    atomic_json_write(DATA / 'tasks_source.json', tasks)
    # Trigger refresh (异步，不阻塞，避免僵尸进程)
    def _refresh():
        try:
            subprocess.run(['python3', str(SCRIPTS / 'refresh_live_data.py')], timeout=30)
        except Exception as e:
            log.warning(f'refresh_live_data.py 触发失败: {e}')
    threading.Thread(target=_refresh, daemon=True).start()


def handle_task_action(task_id, action, reason):
    """Stop/cancel/resume a task from the dashboard."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    old_state = task.get('state', '')
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'task-action-before-{action}')

    if action == 'stop':
        task['state'] = 'Blocked'
        task['block'] = reason or '皇上叫停'
        task['now'] = f'⏸️ 已暂停：{reason}'
    elif action == 'cancel':
        task['state'] = 'Cancelled'
        task['block'] = reason or '皇上取消'
        task['now'] = f'🚫 已取消：{reason}'
    elif action == 'resume':
        # Resume to previous active state or Doing
        task['state'] = task.get('_prev_state', 'Doing')
        task['block'] = '无'
        task['now'] = f'▶️ 已恢复执行'

    if action in ('stop', 'cancel'):
        task['_prev_state'] = old_state  # Save for resume

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '皇上',
        'to': task.get('org', ''),
        'remark': f'{"⏸️ 叫停" if action == "stop" else "🚫 取消" if action == "cancel" else "▶️ 恢复"}：{reason}'
    })

    if action == 'resume':
        _scheduler_mark_progress(task, f'恢复到 {task.get("state", "Doing")}')
    else:
        _scheduler_add_flow(task, f'皇上{action}：{reason or "无"}')

    task['updatedAt'] = now_iso()

    save_tasks(tasks)
    if action == 'resume' and task.get('state') not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, task.get('state'), trigger='resume')
    label = {'stop': '已叫停', 'cancel': '已取消', 'resume': '已恢复'}[action]
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_archive_task(task_id, archived, archive_all_done=False):
    """Archive or unarchive a task, or batch-archive all Done/Cancelled tasks."""
    tasks = load_tasks()
    if archive_all_done:
        count = 0
        for t in tasks:
            if t.get('state') in ('Done', 'Cancelled') and not t.get('archived'):
                t['archived'] = True
                t['archivedAt'] = now_iso()
                count += 1
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道旨意已归档', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    task['archived'] = archived
    if archived:
        task['archivedAt'] = now_iso()
    else:
        task.pop('archivedAt', None)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    label = '已归档' if archived else '已取消归档'
    return {'ok': True, 'message': f'{task_id} {label}'}


def update_task_todos(task_id, todos):
    """Update the todos list for a task."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    task['todos'] = todos
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} todos 已更新'}


def read_skill_content(agent_id, skill_name):
    """Read SKILL.md content for a specific skill."""
    # 输入校验：防止路径遍历
    if not _SAFE_NAME_RE.match(agent_id) or not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': '参数含非法字符'}
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    ag = next((a for a in agents if a.get('id') == agent_id), None)
    if not ag:
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    sk = next((s for s in ag.get('skills', []) if s.get('name') == skill_name), None)
    if not sk:
        return {'ok': False, 'error': f'技能 {skill_name} 不存在'}
    skill_path = pathlib.Path(sk.get('path', '')).resolve()
    # 路径遍历保护：确保路径在 OCLAW_HOME 或项目目录下
    allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
    if not any(str(skill_path).startswith(str(root)) for root in allowed_roots):
        return {'ok': False, 'error': '路径不在允许的目录范围内'}
    if not skill_path.exists():
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': '(SKILL.md 文件不存在)', 'path': str(skill_path)}
    try:
        content = skill_path.read_text()
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': content, 'path': str(skill_path)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def add_skill_to_agent(agent_id, skill_name, description, trigger=''):
    """Create a new skill for an agent with a standardised SKILL.md template."""
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skill_name 含非法字符: {skill_name}'}
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    desc_line = description or skill_name
    trigger_section = f'\n## 触发条件\n{trigger}\n' if trigger else ''
    template = (f'---\n'
                f'name: {skill_name}\n'
                f'description: {desc_line}\n'
                f'---\n\n'
                f'# {skill_name}\n\n'
                f'{desc_line}\n'
                f'{trigger_section}\n'
                f'## 输入\n\n'
                f'<!-- 说明此技能接收什么输入 -->\n\n'
                f'## 处理流程\n\n'
                f'1. 步骤一\n'
                f'2. 步骤二\n\n'
                f'## 输出规范\n\n'
                f'<!-- 说明产出物格式与交付要求 -->\n\n'
                f'## 注意事项\n\n'
                f'- (在此补充约束、限制或特殊规则)\n')
    skill_md.write_text(template)
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    return {'ok': True, 'message': f'技能 {skill_name} 已添加到 {agent_id}', 'path': str(skill_md)}


def add_remote_skill(agent_id, skill_name, source_url, description=''):
    """从远程 URL 或本地路径为 Agent 添加 skill SKILL.md 文件。
    
    支持的源：
    - HTTPS URLs: https://raw.githubusercontent.com/...
    - 本地路径: /path/to/SKILL.md 或 file:///path/to/SKILL.md
    """
    # 输入校验
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    if not source_url or not isinstance(source_url, str):
        return {'ok': False, 'error': 'sourceUrl 必须是有效的字符串'}
    
    source_url = source_url.strip()
    
    # 检查 Agent 是否存在
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    if not any(a.get('id') == agent_id for a in agents):
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    
    # 下载或读取文件内容
    try:
        if source_url.startswith('http://') or source_url.startswith('https://'):
            # HTTPS URL 校验
            if not validate_url(source_url, allowed_schemes=('https',)):
                return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
            
            # 从 URL 下载，带超时保护
            req = Request(source_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            try:
                resp = urlopen(req, timeout=10)
                content = resp.read(10 * 1024 * 1024).decode('utf-8')  # 最多 10MB
                if len(content) > 10 * 1024 * 1024:
                    return {'ok': False, 'error': '文件过大（最大 10MB）'}
            except Exception as e:
                return {'ok': False, 'error': f'URL 无法访问: {str(e)[:100]}'}
        
        elif source_url.startswith('file://'):
            # file:// URL 格式
            local_path = pathlib.Path(source_url[7:])
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            content = local_path.read_text()
        
        elif source_url.startswith('/') or source_url.startswith('.'):
            # 本地绝对或相对路径
            local_path = pathlib.Path(source_url).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root)) for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        
        else:
            return {'ok': False, 'error': '不支持的 URL 格式（仅支持 https://, file://, 或本地路径）'}
    except Exception as e:
        return {'ok': False, 'error': f'文件读取失败: {str(e)[:100]}'}
    
    # 基础验证：检查是否为 Markdown 且包含 YAML frontmatter
    if not content.startswith('---'):
        return {'ok': False, 'error': '文件格式无效（缺少 YAML frontmatter）'}
    
    # 尝试解析 frontmatter
    try:
        import yaml
        parts = content.split('---', 2)
        if len(parts) < 3:
            return {'ok': False, 'error': '文件格式无效（YAML frontmatter 结构错误）'}
        frontmatter_str = parts[1]
        yaml.safe_load(frontmatter_str)  # 验证 YAML 格式
    except Exception as e:
        # 不要求完全的 YAML 解析，但要检查基本结构
        if 'name:' not in content[:500]:
            return {'ok': False, 'error': f'文件格式无效: {str(e)[:100]}'}
    
    # 创建本地目录
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    
    # 写入 SKILL.md
    skill_md.write_text(content)
    
    # 保存源信息到 .source.json
    source_info = {
        'skillName': skill_name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    
    return {
        'ok': True,
        'message': f'技能 {skill_name} 已从远程源添加到 {agent_id}',
        'skillName': skill_name,
        'agentId': agent_id,
        'source': source_url,
        'localPath': str(skill_md),
        'size': len(content),
        'addedAt': now_iso(),
    }


def get_remote_skills_list():
    """列表所有已添加的远程 skills 及其源信息"""
    remote_skills = []
    
    # 遍历所有 workspace
    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            source_json = skill_dir / '.source.json'
            skill_md = skill_dir / 'SKILL.md'
            
            if not source_json.exists():
                # 本地创建的 skill，跳过
                continue
            
            try:
                source_info = json.loads(source_json.read_text())
                # 检查 SKILL.md 是否存在
                status = 'valid' if skill_md.exists() else 'not-found'
                remote_skills.append({
                    'skillName': skill_name,
                    'agentId': agent_id,
                    'sourceUrl': source_info.get('sourceUrl', ''),
                    'description': source_info.get('description', ''),
                    'localPath': str(skill_md),
                    'addedAt': source_info.get('addedAt', ''),
                    'lastUpdated': source_info.get('lastUpdated', ''),
                    'status': status,
                })
            except Exception:
                pass
    
    return {
        'ok': True,
        'remoteSkills': remote_skills,
        'count': len(remote_skills),
        'listedAt': now_iso(),
    }


def update_remote_skill(agent_id, skill_name):
    """更新已添加的远程 skill 为最新版本（重新从源 URL 下载）"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    source_json = workspace / '.source.json'
    skill_md = workspace / 'SKILL.md'
    
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill（无 .source.json）'}
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl', '')
        if not source_url:
            return {'ok': False, 'error': '源 URL 不存在'}
        
        # 重新下载
        result = add_remote_skill(agent_id, skill_name, source_url, 
                                  source_info.get('description', ''))
        if result['ok']:
            result['message'] = f'技能已更新'
            source_info_updated = json.loads(source_json.read_text())
            result['newVersion'] = source_info_updated.get('checksum', 'unknown')
        return result
    except Exception as e:
        return {'ok': False, 'error': f'更新失败: {str(e)[:100]}'}


def remove_remote_skill(agent_id, skill_name):
    """移除已添加的远程 skill"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    if not workspace.exists():
        return {'ok': False, 'error': f'技能不存在: {skill_name}'}
    
    # 检查是否为远程 skill
    source_json = workspace / '.source.json'
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill，无法通过此 API 移除'}
    
    try:
        # 删除整个 skill 目录
        import shutil
        shutil.rmtree(workspace)
        
        # Re-sync agent config
        try:
            subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
        except Exception:
            pass
        
        return {'ok': True, 'message': f'技能 {skill_name} 已从 {agent_id} 移除'}
    except Exception as e:
        return {'ok': False, 'error': f'移除失败: {str(e)[:100]}'}


def _compute_checksum(content: str) -> str:
    """计算内容的简单校验和（SHA256 的前16字符）"""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def push_to_feishu():
    """Push morning brief link to Feishu via webhook."""
    cfg = read_json(DATA / 'morning_brief_config.json', {})
    webhook = cfg.get('feishu_webhook', '').strip()
    if not webhook:
        return
    if not validate_url(webhook, allowed_schemes=('https',), allowed_domains=('open.feishu.cn', 'open.larksuite.com')):
        log.warning(f'飞书 Webhook URL 不合法: {webhook}')
        return
    brief = read_json(DATA / 'morning_brief.json', {})
    date_str = brief.get('date', '')
    total = sum(len(v) for v in (brief.get('categories') or {}).values())
    if not total:
        return
    cat_lines = []
    for cat, items in (brief.get('categories') or {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    summary = '\n'.join(cat_lines)
    date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
    payload = json.dumps({
        'msg_type': 'interactive',
        'card': {
            'header': {'title': {'tag': 'plain_text', 'content': f'📰 天下要闻 · {date_fmt}'}, 'template': 'blue'},
            'elements': [
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'共 **{total}** 条要闻已更新\n{summary}'}},
                {'tag': 'action', 'actions': [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': '🔗 查看完整简报'}, 'url': 'http://127.0.0.1:7891', 'type': 'primary'}]},
                {'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"采集于 {brief.get('generated_at', '')}"}]}
            ]
        }
    }).encode()
    try:
        req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
        resp = urlopen(req, timeout=10)
        print(f'[飞书] 推送成功 ({resp.status})')
    except Exception as e:
        print(f'[飞书] 推送失败: {e}', file=sys.stderr)


def push_to_telegram(message=''):
    """Push message to Telegram via Bot API."""
    cfg = read_json(DATA / 'telegram_push_config.json', {})
    bot_token = cfg.get('bot_token', '').strip()
    chat_id = cfg.get('chat_id', '').strip()
    
    if not bot_token or not chat_id:
        return {'ok': False, 'error': 'Telegram 未配置 (bot_token 或 chat_id 缺失)'}
    
    # 构建消息内容
    if not message:
        # 如果没有传入消息，使用早报摘要
        brief = read_json(DATA / 'morning_brief.json', {})
        date_str = brief.get('date', '')
        total = sum(len(v) for v in (brief.get('categories') or {}).values())
        
        if date_str and total:
            date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
            cat_lines = []
            for cat, items in (brief.get('categories') or {}).items():
                if items:
                    cat_lines.append(f'  {cat}: {len(items)} 条')
            summary = '\n'.join(cat_lines) if cat_lines else '暂无内容'
            
            message = (
                f"📰 *天下要闻 · {date_fmt}*\n\n"
                f"共 **{total}** 条要闻已更新\n\n"
                f"{summary}\n\n"
                f"🔗 [查看完整简报](http://127.0.0.1:7891)"
            )
        else:
            message = "📋 三省六部看板更新通知"
    
    # 发送 Telegram 消息
    try:
        import urllib.request
        import urllib.error
        
        url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
        payload = json.dumps({
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': False
        }).encode('utf-8')
        
        req = Request(url, data=payload, headers={
            'Content-Type': 'application/json',
            'User-Agent': 'AgentTeamsBI/1.0'
        })
        resp = urlopen(req, timeout=15)
        result = json.loads(resp.read().decode('utf-8'))
        
        if result.get('ok'):
            print(f'[Telegram] 推送成功')
            return {'ok': True, 'message': '推送成功'}
        else:
            print(f'[Telegram] 推送失败: {result.get("description", "未知错误")}')
            return {'ok': False, 'error': result.get('description', '未知错误')}
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        print(f'[Telegram] HTTP错误 {e.code}: {error_body}', file=sys.stderr)
        return {'ok': False, 'error': f'HTTP {e.code}: {error_body[:200]}'}
    except Exception as e:
        print(f'[Telegram] 推送失败: {e}', file=sys.stderr)
        return {'ok': False, 'error': str(e)}


def test_telegram_config():
    """测试 Telegram 配置是否正确"""
    cfg = read_json(DATA / 'telegram_push_config.json', {})
    bot_token = cfg.get('bot_token', '').strip()
    chat_id = cfg.get('chat_id', '').strip()
    
    if not bot_token:
        return {'ok': False, 'error': 'bot_token 不能为空'}
    if not chat_id:
        return {'ok': False, 'error': 'chat_id 不能为空'}
    
    try:
        import urllib.request
        import urllib.error
        
        # 获取 Bot 信息
        url = f'https://api.telegram.org/bot{bot_token}/getMe'
        req = Request(url, headers={'User-Agent': 'AgentTeamsBI/1.0'})
        resp = urlopen(req, timeout=10)
        result = json.loads(resp.read().decode('utf-8'))
        
        if not result.get('ok'):
            return {'ok': False, 'error': 'Bot Token 无效'}
        
        bot_name = result.get('result', {}).get('first_name', 'Unknown')
        
        # 验证 chat_id（支持用户ID或群组ID）
        url = f'https://api.telegram.org/bot{bot_token}/getChat?chat_id={chat_id}'
        req = Request(url, headers={'User-Agent': 'AgentTeamsBI/1.0'})
        resp = urlopen(req, timeout=10)
        chat_result = json.loads(resp.read().decode('utf-8'))
        
        if not chat_result.get('ok'):
            return {'ok': False, 'error': f'chat_id 无效: {chat_result.get("description", "")}'}
        
        chat_name = chat_result.get('result', {}).get('title') or chat_result.get('result', {}).get('username', 'Unknown')
        
        return {
            'ok': True,
            'message': f'配置成功！Bot: @{bot_name}, 目标: {chat_name}'
        }
        
    except urllib.error.HTTPError as e:
        return {'ok': False, 'error': f'HTTP错误 {e.code}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# 旨意标题最低要求
_MIN_TITLE_LEN = 10
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def handle_create_task(title, org='中书省', official='中书令', priority='normal', template_id='', params=None, target_dept=''):
    """从看板创建新任务（圣旨模板下旨）。"""
    if not title or not title.strip():
        return {'ok': False, 'error': '任务标题不能为空'}
    title = title.strip()
    # 剥离 Conversation info 元数据
    title = re.split(r'\n*Conversation info\s*\(', title, maxsplit=1)[0].strip()
    title = re.split(r'\n*```', title, maxsplit=1)[0].strip()
    # 清理常见前缀: "传旨:" "下旨:" 等
    title = re.sub(r'^(传旨|下旨)[：:\uff1a]\s*', '', title)
    if len(title) > 100:
        title = title[:100] + '…'
    # 标题质量校验：防止闲聊被误建为旨意
    if len(title) < _MIN_TITLE_LEN:
        return {'ok': False, 'error': f'标题过短（{len(title)}<{_MIN_TITLE_LEN}字），不像是旨意'}
    if title.lower() in _JUNK_TITLES:
        return {'ok': False, 'error': f'「{title}」不是有效旨意，请输入具体工作指令'}
    # 生成 task id: JJC-YYYYMMDD-NNN
    today = datetime.datetime.now().strftime('%Y%m%d')
    tasks = load_tasks()
    today_ids = [t['id'] for t in tasks if t.get('id', '').startswith(f'JJC-{today}-')]
    seq = 1
    if today_ids:
        nums = [int(tid.split('-')[-1]) for tid in today_ids if tid.split('-')[-1].isdigit()]
        seq = max(nums) + 1 if nums else 1
    task_id = f'JJC-{today}-{seq:03d}'
    # 正确流程起点：皇上 -> 太子分拣
    # target_dept 记录模板建议的最终执行部门（仅供尚书省派发参考）
    initial_org = '太子'
    new_task = {
        'id': task_id,
        'title': title,
        'official': official,
        'org': initial_org,
        'state': 'Taizi',
        'now': '等待太子接旨分拣',
        'eta': '-',
        'block': '无',
        'output': '',
        'ac': '',
        'priority': priority,
        'templateId': template_id,
        'templateParams': params or {},
        'flow_log': [{
            'at': now_iso(),
            'from': '皇上',
            'to': initial_org,
            'remark': f'下旨：{title}'
        }],
        'updatedAt': now_iso(),
    }
    if target_dept:
        new_task['targetDept'] = target_dept

    _ensure_scheduler(new_task)
    _scheduler_snapshot(new_task, 'create-task-initial')
    _scheduler_mark_progress(new_task, '任务创建')

    tasks.insert(0, new_task)
    save_tasks(tasks)
    log.info(f'创建任务: {task_id} | {title[:40]}')

    dispatch_for_state(task_id, new_task, 'Taizi', trigger='imperial-edict')

    return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，正在派发给太子'}


def handle_review_action(task_id, action, comment=''):
    """门下省御批：准奏/封驳。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if task.get('state') not in ('Review', 'Menxia'):
        return {'ok': False, 'error': f'任务 {task_id} 当前状态为 {task.get("state")}，无法御批'}

    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'review-before-{action}')

    if action == 'approve':
        if task['state'] == 'Menxia':
            task['state'] = 'Assigned'
            task['now'] = '门下省准奏，移交尚书省派发'
            remark = f'✅ 准奏：{comment or "门下省审议通过"}'
            to_dept = '尚书省'
        else:  # Review
            task['state'] = 'Done'
            task['now'] = '御批通过，任务完成'
            remark = f'✅ 御批准奏：{comment or "审查通过"}'
            to_dept = '皇上'
    elif action == 'reject':
        round_num = (task.get('review_round') or 0) + 1
        task['review_round'] = round_num
        task['state'] = 'Zhongshu'
        task['now'] = f'封驳退回中书省修订（第{round_num}轮）'
        remark = f'🚫 封驳：{comment or "需要修改"}'
        to_dept = '中书省'
    else:
        return {'ok': False, 'error': f'未知操作: {action}'}

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '门下省' if task.get('state') != 'Done' else '皇上',
        'to': to_dept,
        'remark': remark
    })
    _scheduler_mark_progress(task, f'审议动作 {action} -> {task.get("state")}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    # 🚀 审批后自动派发对应 Agent
    new_state = task['state']
    if new_state not in ('Done',):
        dispatch_for_state(task_id, task, new_state)

    label = '已准奏' if action == 'approve' else '已封驳'
    dispatched = ' (已自动派发 Agent)' if new_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {label}{dispatched}'}


# ══ Agent 在线状态检测 ══

# 默认配置（备用）
_DEFAULT_AGENT_DEPTS = [
    {'id':'main',      'label':'总指挥', 'emoji':'🎯', 'role':'任务协调',   'rank':'总指挥'},
    {'id':'creator',   'label':'笔杆子', 'emoji':'✍️', 'role':'内容创作',   'rank':'笔杆子'},
    {'id':'canmou',    'label':'参谋',   'emoji':'🔬', 'role':'深度分析',   'rank':'参谋'},
    {'id':'yunying',   'label':'运营官','emoji':'📋', 'role':'日常运营',   'rank':'运营官'},
    {'id':'evolver',   'label':'进化官','emoji':'🧬', 'role':'技术进化',   'rank':'进化官'},
    {'id':'trader',    'label':'交易官','emoji':'📈', 'role':'交易监控',   'rank':'交易官'},
    {'id':'community', 'label':'社区官','emoji':'💬', 'role':'社区运营',   'rank':'社区官'},
]

def _load_agents_from_md():
    """从 AGENTS.md 动态读取 Agent 配置"""
    import re
    agents = []
    agents_md = pathlib.Path('C:/Users/Administrator/.openclaw/workspace/AGENTS.md')
    
    if not agents_md.exists():
        return _DEFAULT_AGENT_DEPTS
    
    try:
        content = agents_md.read_text(encoding='utf-8')
        
        # 匹配每个 Agent 块：### ✍️ 笔杆子 (creator)
        agent_blocks = re.split(r'^###\s+[^\(]+\s+\((\w+)\)', content, flags=re.MULTILINE)
        
        for i in range(1, len(agent_blocks), 2):
            if i+1 < len(agent_blocks):
                aid = agent_blocks[i]
                block = agent_blocks[i+1]
                
                # 提取 emoji 和 label
                emoji_match = re.search(r'^###\s+([^\(]+)\s+\(', content[content.find(aid)-200:content.find(aid)+50])
                
                # 提取角色：**角色**：xxxx
                role_match = re.search(r'\*\*角色\*\*[：:]\s*(.+)', block)
                role = role_match.group(1).strip() if role_match else ''
                
                # 提取职责列表
                duties = []
                duty_matches = re.findall(r'^\s*-\s*(.+?)(?:\n|$)', block, re.MULTILINE)
                duties = [d.strip() for d in duty_matches[:3]]  # 取前3条
                
                # 找对应的默认配置获取 emoji
                default = next((a for a in _DEFAULT_AGENT_DEPTS if a['id'] == aid), None)
                emoji = default['emoji'] if default else '❓'
                label = default['label'] if default else aid
                rank = default['rank'] if default else aid
                
                agents.append({
                    'id': aid,
                    'label': label,
                    'emoji': emoji,
                    'role': role,
                    'rank': rank,
                    'duties': duties
                })
        
        return agents if agents else _DEFAULT_AGENT_DEPTS
        
    except Exception as e:
        print(f"[AGENTS.md] 读取失败: {e}")
        return _DEFAULT_AGENT_DEPTS

# 启动时加载
_AGENT_DEPTS = _load_agents_from_md()
_AGENTS_MD_PATH = pathlib.Path('C:/Users/Administrator/.openclaw/workspace/AGENTS.md')
_agents_md_mtime = _AGENTS_MD_PATH.stat().st_mtime if _AGENTS_MD_PATH.exists() else 0
print(f"[AGENTS.md] 已加载 {len(_AGENT_DEPTS)} 个 Agent 配置")

# 排位数据加载
_MEMORY_MD_PATH = pathlib.Path('C:/Users/Administrator/.openclaw/workspace/MEMORY.md')
_rankings = []

def _load_rankings_from_md():
    """从 MEMORY.md 读取排位数据"""
    import re
    rankings = []
    try:
        if not _MEMORY_MD_PATH.exists():
            return rankings
        content = _MEMORY_MD_PATH.read_text(encoding='utf-8')
        
        # 查找排位表格
        # 格式: | 总指挥 | 1300 | 白银3 |
        pattern = r'\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*(\S+)\s*\|'
        matches = re.findall(pattern, content)
        
        # 按 LP 分数排序
        for name, lp, tier in matches:
            rankings.append({'name': name, 'lp': int(lp), 'tier': tier})
        
        rankings.sort(key=lambda x: x['lp'], reverse=True)
        return rankings
    except Exception as e:
        print(f"[MEMORY.md] 排位读取失败: {e}")
        return rankings

def _get_rankings():
    """获取排位数据（带缓存）"""
    global _rankings
    return _rankings

# 初始化排位数据
_rankings = _load_rankings_from_md()
_memory_md_mtime = _MEMORY_MD_PATH.stat().st_mtime if _MEMORY_MD_PATH.exists() else 0
print(f"[MEMORY.md] 已加载 {len(_rankings)} 条排位记录")

# 实时事件系统
_events = []
_prev_agent_status = {}  # 上一次的 Agent 状态
_events_initialized = False
_prev_gateway_alive = None  # 上一次 Gateway 状态
_seen_users = set()  # 已见过的用户
_reported_event_ids = set()  # 已报告过的事件ID（去重用）

def _parse_timestamp(ts_str):
    """解析 ISO 时间戳，返回 HH:MM:SS 格式（北京时间+8）"""
    try:
        # 格式: 2026-03-13T15:50:40.657Z
        if 'T' in ts_str and ts_str.endswith('Z'):
            # 解析 ISO 时间并转换为北京时间 (+8)
            utc_time = datetime.datetime.fromisoformat(ts_str.rstrip('Z'))
            beijing_time = utc_time + datetime.timedelta(hours=8)
            return beijing_time.strftime('%H:%M:%S')
    except:
        pass
    return datetime.datetime.now().strftime('%H:%M:%S')

def _get_sort_key(ts_str):
    """解析 ISO 时间戳，返回用于排序的完整时间字符串"""
    try:
        # 直接返回原始时间用于排序，它本身就是 ISO 格式
        return ts_str
    except:
        pass
    return datetime.datetime.now().isoformat()

def _init_events():
    """初始化系统事件"""
    global _events, _events_initialized, _seen_users, _reported_event_ids
    if _events_initialized:
        return
    _events_initialized = True
    
    # 清空旧事件和记录
    _events = []
    _reported_event_ids = set()
    
    # 初始事件列表（暂不添加默认事件，只显示真实事件）
    _events = []
    
    # 初始化已见过的用户
    _init_seen_users()

def _init_seen_users():
    """初始化已知用户列表"""
    global _seen_users
    try:
        sessions_path = pathlib.Path('C:/Users/Administrator/.openclaw/agents/main/sessions')
        if sessions_path.exists():
            for f in sessions_path.glob('*.jsonl'):
                try:
                    content = f.read_text(encoding='utf-8')
                    # 尝试从中提取用户ID
                    import re
                    users = re.findall(r'"user_id"\s*:\s*"([^"]+)"', content)
                    _seen_users.update(users)
                except:
                    pass
    except:
        pass

def _generate_events():
    """根据各种状态变化生成事件"""
    global _events, _task_events, _prev_agent_status, _prev_gateway_alive, _seen_users, _task_events_initialized
    now_str = datetime.datetime.now().strftime('%H:%M:%S')
    new_events = []
    new_task_events = []
    
    try:
        # 1. Gateway 状态变化
        gateway_alive = _check_gateway_alive()
        if _prev_gateway_alive is not None and _prev_gateway_alive != gateway_alive:
            if gateway_alive:
                new_events.append({
                    'time': now_str,
                    'title': 'Gateway 在线',
                    'desc': 'Gateway 服务已连接',
                    'type': 'success'
                })
            else:
                new_events.append({
                    'time': now_str,
                    'title': 'Gateway 离线',
                    'desc': 'Gateway 服务断开连接',
                    'type': 'error'
                })
        _prev_gateway_alive = gateway_alive
        
        # 2. 检查新用户
        try:
            sessions_path = pathlib.Path('C:/Users/Administrator/.openclaw/agents/main/sessions')
            if sessions_path.exists():
                for f in sessions_path.glob('*.jsonl'):
                    if '.deleted.' in f.name:
                        continue
                    try:
                        content = f.read_text(encoding='utf-8')
                        import re
                        users = re.findall(r'"user_id"\s*:\s*"([^"]+)"', content)
                        for uid in users:
                            if uid and uid not in _seen_users:
                                _seen_users.add(uid)
                                # 尝试获取用户名
                                name_match = re.search(r'"sender"\s*:\s*"([^"]+)"', content)
                                name = name_match.group(1) if name_match else uid
                                new_events.append({
                                    'time': now_str,
                                    'title': '新用户加入',
                                    'desc': f'新用户: {name}',
                                    'type': 'info'
                                })
                    except:
                        pass
        except:
            pass
        
        # 3. 检查消息、错误（从最新会话中读取，带时间戳和去重）
        try:
            sessions_path = pathlib.Path('C:/Users/Administrator/.openclaw/agents/main/sessions')
            if sessions_path.exists():
                # 获取最新的1个会话文件，只读取最后10KB（最新内容）
                files = sorted(sessions_path.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True)
                for f in list(files)[:1]:
                    if '.deleted.' in f.name:
                        continue
                    try:
                        # 读取最新的 session 文件的最后600KB，加快速度
                        file_size = f.stat().st_size
                        read_size = min(2000 * 1024, file_size)  # 2MB，覆盖整个文件
                        with open(f, 'rb') as fp:
                            fp.seek(file_size - read_size)
                            content = fp.read().decode('utf-8')
                        
                        # 消息检测 - 只检查最新的1条 role:user 消息
                        user_msgs = re.finditer(r'"timestamp"\s*:\s*"([^"]+)".*?"role"\s*:\s*"user"', content, re.DOTALL)
                        latest_ts = None
                        for match in user_msgs:
                            ts = match.group(1)
                            if latest_ts is None or ts > latest_ts:
                                latest_ts = ts
                        
                        if latest_ts:
                            event_id = f"msg_{latest_ts}"
                            if event_id not in _reported_event_ids:
                                _reported_event_ids.add(event_id)
                                msg_time = _parse_timestamp(latest_ts)
                                new_events.append({
                                    'time': msg_time,
                                    'sort_key': latest_ts,
                                    'title': '消息接收',
                                    'desc': '收到用户消息',
                                    'type': 'info'
                                })
                        
                        # 错误检测 - 用错误首次出现的时间
                        error_matches = re.findall(r'(?:{"type":"message".*?"timestamp"\s*:\s*"([^"]+)".*?"error"\s*:\s*"([^"]+)}|"error"\s*:\s*"([^"]+)"\s*,.*?"timestamp"\s*:\s*"([^"]+)"))', content, re.DOTALL)
                        for match in error_matches[-3:]:
                            # 解析出时间和错误信息
                            ts, err1, err2, ts2 = match[0], match[1], match[2], match[3]
                            if not ts:
                                ts = ts2
                            err = err1 or err2
                            if ts and err and len(err) > 2:
                                err_time = _parse_timestamp(ts)
                                event_id = f"err_{err[:50]}"
                                if event_id not in _reported_event_ids:
                                    _reported_event_ids.add(event_id)
                                    new_events.append({
                                        'time': err_time,
                                        'sort_key': ts,
                                        'title': '错误/异常',
                                        'desc': f'错误: {err[:30]}',
                                        'type': 'error'
                                    })
                        
                        # ===== 任务分配 & 任务完成 检测现在使用本地JSON文件 =====
                        # 不再从session文件解析，直接从task_events.json读取
                            
                            # 解码unicode
                            try:
                                import json as json_module
                                txt = json_module.loads(f'"{txt_raw}"')
                            except:
                                txt = txt_raw
                            
                            # 不再从session文件解析任务事件（现在用本地JSON文件）
                                
                    except:
                        pass
        except:
            pass
        
        # 4. Agent 状态变化（已有的逻辑）
        current_status = get_agents_status()
        if current_status.get('ok'):
            agents = current_status.get('agents', [])
            
            for agent in agents:
                aid = agent.get('id', '')
                status = agent.get('status', 'offline')
                label = agent.get('label', aid)
                
                prev_status = _prev_agent_status.get(aid)
                
                if prev_status is not None and prev_status != status:
                    # 状态转换: 离线↔空闲↔忙碌/在线
                    status_map = {'offline': '离线', 'idle': '空闲', 'online': '在线', 'busy': '忙碌', 'running': '忙碌'}
                    from_status = status_map.get(prev_status, prev_status)
                    to_status = status_map.get(status, status)
                    new_events.append({
                        'time': now_str,
                        'sort_key': now_iso(),
                        'title': 'Agent 状态',
                        'desc': f'< {label} > {from_status} → {to_status}',
                        'type': 'info' if status == 'idle' else 'success' if status != 'offline' else 'warning'
                    })
                
                # 会话数变化
                sessions = agent.get('sessions', 0)
                prev_sessions = _prev_agent_status.get(f'{aid}_sessions')
                if prev_sessions is not None and prev_sessions != sessions:
                    if sessions > prev_sessions:
                        new_events.append({
                            'time': now_str,
                            'sort_key': now_iso(),
                            'title': '新会话',
                            'desc': f'< {label} > 新建会话 (当前 {sessions} 个)',
                            'type': 'info'
                        })
                
                _prev_agent_status[aid] = status
                _prev_agent_status[f'{aid}_sessions'] = sessions
        
        # 添加新事件（保留最近30条，去重）
        if new_events:
            _events = (new_events + _events)[:30]
            
    except Exception as e:
        # 记录到日志文件
        import traceback
        try:
            with open(r'C:\Users\Administrator\.openclaw\workspace\gen_error.log', 'a', encoding='utf-8') as f:
                f.write(f"{datetime.datetime.now()} Error: {e}\n{traceback.format_exc()}\n")
        except:
            pass
        pass

def _get_events():
    """获取事件列表（按时间降序）"""
    global _events
    
    # 用 ISO 时间解析后排序，不是字符串比较
    def sort_value(x):
        try:
            if 'sort_key' in x:
                # 解析 ISO 格式: 2026-03-13T16:55:55.869Z
                ts = x['sort_key'].replace('Z', '+00:00')
                dt = datetime.datetime.fromisoformat(ts)
                # 转成 naive datetime 用于比较（去掉时区）
                return dt.replace(tzinfo=None)
        except:
            pass
        try:
            # 备用：解析 HH:MM:SS 格式
            return datetime.datetime.strptime(x['time'], '%H:%M:%S')
        except:
            pass
        return datetime.datetime.min
    
    result = sorted(_events, key=sort_value, reverse=True)
    return result[:30]

def _reload_rankings_if_needed():
    """检查 MEMORY.md 是否有变化，如有则重新加载排位"""
    global _rankings, _memory_md_mtime
    try:
        if not _MEMORY_MD_PATH.exists():
            return
        current_mtime = _MEMORY_MD_PATH.stat().st_mtime
        if current_mtime != _memory_md_mtime:
            _memory_md_mtime = current_mtime
            _rankings = _load_rankings_from_md()
            print(f"[MEMORY.md] 文件变化，已重新加载 {len(_rankings)} 条排位")
    except Exception as e:
        pass

def _reload_agents_if_needed():
    """检查 AGENTS.md 是否有变化，如有则重新加载"""
    global _AGENT_DEPTS, _agents_md_mtime
    try:
        if not _AGENTS_MD_PATH.exists():
            return
        current_mtime = _AGENTS_MD_PATH.stat().st_mtime
        print(f"[DEBUG] current_mtime={current_mtime}, saved={_agents_md_mtime}, diff={current_mtime - _agents_md_mtime}")
        if current_mtime != _agents_md_mtime:
            _agents_md_mtime = current_mtime
            _AGENT_DEPTS = _load_agents_from_md()
            print(f"[AGENTS.md] 文件变化，已重新加载 {len(_AGENT_DEPTS)} 个 Agent")
    except Exception as e:
        print(f"[DEBUG] error: {e}")


def _check_gateway_alive():
    """检测 Gateway 是否在运行（通过 HTTP probe）。"""
    try:
        from urllib.request import urlopen
        resp = urlopen('http://127.0.0.1:18789/', timeout=3)
        return resp.status == 200
    except Exception:
        return False


def _check_gateway_probe():
    """通过 HTTP probe 检测 Gateway 是否响应。"""
    try:
        from urllib.request import urlopen
        resp = urlopen('http://127.0.0.1:18789/', timeout=3)
        return resp.status == 200
    except Exception:
        return False


def _get_agent_session_status(agent_id):
    """读取 Agent 的 sessions.json 获取活跃状态。
    返回: (last_active_ts_ms, session_count, is_busy)
    """
    sessions_file = OCLAW_HOME / 'agents' / agent_id / 'sessions' / 'sessions.json'
    if not sessions_file.exists():
        return 0, 0, False
    try:
        data = json.loads(sessions_file.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for v in data.values():
            ts = v.get('updatedAt', 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = ts
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        is_busy = age_ms <= 2 * 60 * 1000  # 2分钟内视为正在工作
        return last_ts, session_count, is_busy
    except Exception:
        return 0, 0, False


def _get_task_status(agent_id):
    """根据task_events.json判断Agent状态。
    返回: (latest_dispatch_ms, latest_complete_ms, last_activity_ms)
    - latest_dispatch_ms: 最近一次任务分配时间（毫秒），0表示5分钟内没有
    - latest_complete_ms: 最近一次任务完成时间（毫秒），0表示5分钟内没有
    - last_activity_ms: 最后活动时间（毫秒），不限于5分钟
    """
    try:
        task_file = pathlib.Path(__file__).parent / 'task_events.json'
        if not task_file.exists():
            return False, False, 0
        
        events = json.loads(task_file.read_text(encoding='utf-8'))
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        five_min_ms = 5 * 60 * 1000
        
        latest_dispatch = 0
        latest_complete = 0
        last_activity = 0
        
        # Agent名映射（dept id -> 显示名）
        agent_map = {
            'evolver': '进化官',
            'creator': '笔杆子',
            'canmou': '参谋',
            'yunying': '运营官',
            'trader': '交易官',
            'community': '社区官',
            'main': '总指挥',
            '总指挥': '总指挥',
            '进化官': '进化官',
            '笔杆子': '笔杆子',
            '参谋': '参谋',
            '运营官': '运营官',
            '交易官': '交易官',
            '社区官': '社区官',
        }
        agent_names = [agent_id, agent_map.get(agent_id, agent_id)]
        
        for e in events:
            # 解析时间
            sort_key = e.get('sort_key', '')
            if not sort_key:
                continue
            
            try:
                # Parse ISO format: 2026-03-14T15:10:00.123456
                event_ts = datetime.datetime.fromisoformat(sort_key.replace('Z', '+00:00'))
                event_ms = int(event_ts.timestamp() * 1000)
            except:
                continue
            
            age_ms = now_ms - event_ms
            if age_ms > five_min_ms:
                continue  # 超过5分钟不关心
            
            if event_ms > last_activity:
                last_activity = event_ms
            
            title = e.get('title', '')
            desc = e.get('desc', '')
            
            # 检查任务分配
            if title == '任务分配':
                for name in agent_names:
                    if name in desc and '派发给' in desc:
                        if event_ms > latest_dispatch:
                            latest_dispatch = event_ms
                        break
            
            # 检查任务完成
            if title == '任务完成':
                for name in agent_names:
                    if f'< {name } >' in desc or desc.startswith(f'{name}完成'):

                        if event_ms > latest_complete: latest_complete = event_ms
                        break
        
        return latest_dispatch, latest_complete, last_activity
    except Exception as e:
        print(f"[_get_task_status] Error: {e}")
        return 0, 0, 0


def _check_agent_process(agent_id):
    """检测是否有该 Agent 的 openclaw-agent 进程正在运行。"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', f'openclaw.*--agent.*{agent_id}'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_agent_workspace(agent_id):
    """检查 Agent 工作空间是否存在。"""
    # main 的工作空间是 workspace，其他是 workspace-{agent_id}
    if agent_id == 'main':
        ws = OCLAW_HOME / 'workspace'
    else:
        ws = OCLAW_HOME / f'workspace-{agent_id}'
    return ws.is_dir()


def get_agents_status():
    """获取所有 Agent 的在线状态。
    返回各 Agent 的:
    - status: 'running' | 'idle' | 'offline' | 'unconfigured'
    - lastActive: 最后活跃时间
    - sessions: 会话数
    - hasWorkspace: 工作空间是否存在
    - processAlive: 是否有进程在运行
    """
    gateway_alive = _check_gateway_alive()
    gateway_probe = _check_gateway_probe() if gateway_alive else False

    agents = []
    seen_ids = set()
    for dept in _AGENT_DEPTS:
        aid = dept['id']
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        has_workspace = _check_agent_workspace(aid)
        last_ts, sess_count, is_busy = _get_agent_session_status(aid)
        process_alive = _check_agent_process(aid)
        
        # 根据task_events.json判断任务状态
        latest_dispatch, latest_complete, task_last_activity = _get_task_status(aid)
        
        # 状态判定（新逻辑：任务驱动 + 时间戳比较）
        # 忙碌：最近任务分配时间 > 最近任务完成时间
        # 在线：最近任务完成时间 > 最近任务分配时间
        # 空闲：超过5分钟无任何活动
        
        # 取最后活动时间（session + task）
        effective_last_ts = last_ts  # 只用session活动判断，不混淆task
        
        # 比较任务分配和完成的时间
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        five_min_ms = 5 * 60 * 1000
        
        has_recent_dispatch = latest_dispatch > 0 and (now_ms - latest_dispatch) <= five_min_ms
        has_recent_complete = latest_complete > 0 and (now_ms - latest_complete) <= five_min_ms
        
        # 状态判定（优先级）：task任务 > session活动 > 无活动
        # 1. 有任务分配 = 忙碌
        # 2. 有任务完成 = 在线
        # 3. session有活动 + 2分钟内有动作 = 忙碌
        # 4. session有活动 + 2-5分钟无动作 = 在线
        # 5. 超过5分钟无任何活动 = 空闲
        
        if not has_workspace:
            status = 'unconfigured'
            status_label = '未配置'
        elif not gateway_alive:
            status = 'idle'
            status_label = '空闲'
        elif has_recent_dispatch and (not has_recent_complete or latest_dispatch > latest_complete):
            # 有任务分配 = 忙碌（优先于session检查）
            status = 'busy'
            status_label = '忙碌'
        elif has_recent_complete and (not has_recent_dispatch or latest_complete >= latest_dispatch):
            # 有任务完成 = 在线
            status = 'online'
            status_label = '在线'
        elif sess_count > 0 and is_busy:
            # 有活跃会话且2分钟内有事做 = 忙碌
            status = 'busy'
            status_label = '忙碌'
        elif sess_count > 0 and last_ts > 0:
            # 有会话但超过2分钟无动作 = 在线
            status = 'online'
            status_label = '在线'
        elif effective_last_ts > 0:
            # 有活动记录，检查是否超过5分钟
            age_ms = now_ms - effective_last_ts
            if age_ms > 5 * 60 * 1000:  # 超过5分钟无活动
                status = 'idle'
                status_label = '空闲'
            else:
                status = 'online'
                status_label = '在线'
        else:
            # 从未有过活动
            status = 'idle'
            status_label = '空闲'

        # 格式化最后活跃时间
        last_active_str = None
        if last_ts > 0:
            try:
                last_active_str = datetime.datetime.fromtimestamp(
                    last_ts / 1000
                ).strftime('%m-%d %H:%M')
            except Exception:
                pass

        # sessions: 显示总活跃会话数（不管忙碌还是在线）
        # 从 sessions.json 读取实际会话数
        task_events_count = sess_count
        
        agents.append({
            'id': aid,
            'label': dept['label'],
            'emoji': dept['emoji'],
            'role': dept['role'],
            'status': status,
            'statusLabel': status_label,
            'lastActive': last_active_str,
            'lastActiveTs': last_ts,
            'sessions': task_events_count,
            'hasWorkspace': has_workspace,
            'processAlive': process_alive,
        })

    return {
        'ok': True,
        'gateway': {
            'alive': gateway_alive,
            'probe': gateway_probe,
            'status': '🟢 运行中' if gateway_probe else ('🟡 进程在但无响应' if gateway_alive else '🔴 未启动'),
        },
        'agents': agents,
        'checkedAt': now_iso(),
    }


def wake_agent(agent_id, message=''):
    """唤醒指定 Agent，发送一条心跳/唤醒消息。"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agent_id 非法: {agent_id}'}
    if not _check_agent_workspace(agent_id):
        return {'ok': False, 'error': f'{agent_id} 工作空间不存在，请先配置'}
    if not _check_gateway_alive():
        return {'ok': False, 'error': 'Gateway 未启动，请先运行 openclaw gateway start'}

    # agent_id 直接作为 runtime_id（openclaw agents list 中的注册名）
    runtime_id = agent_id
    msg = message or f'🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {now_iso()}'

    def do_wake():
        try:
            cmd = ['openclaw', 'agent', '--agent', runtime_id, '-m', msg, '--timeout', '120']
            log.info(f'🔔 唤醒 {agent_id}...')
            # 带重试（最多2次）
            for attempt in range(1, 3):
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=130)
                if result.returncode == 0:
                    log.info(f'✅ {agent_id} 已唤醒')
                    return
                err_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {agent_id} 唤醒失败(第{attempt}次): {err_msg}')
                if attempt < 2:
                    import time
                    time.sleep(5)
            log.error(f'❌ {agent_id} 唤醒最终失败')
        except subprocess.TimeoutExpired:
            log.error(f'❌ {agent_id} 唤醒超时(130s)')
        except Exception as e:
            log.warning(f'⚠️ {agent_id} 唤醒异常: {e}')
    threading.Thread(target=do_wake, daemon=True).start()

    return {'ok': True, 'message': f'{agent_id} 唤醒指令已发出，约10-30秒后生效'}


# ══ Agent 实时活动读取 ══

# 状态 → agent_id 映射
_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,         # 六部，需从 org 推断
    'Review': 'shangshu',
    'Next': None,          # 待执行，从 org 推断
    'Pending': 'zhongshu', # 待处理，默认中书省
}
_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}

_TERMINAL_STATES = {'Done', 'Cancelled'}


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _ensure_scheduler(task):
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    sched.setdefault('enabled', True)
    sched.setdefault('stallThresholdSec', 180)
    sched.setdefault('maxRetry', 1)
    sched.setdefault('retryCount', 0)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('autoRollback', True)
    if not sched.get('lastProgressAt'):
        sched['lastProgressAt'] = task.get('updatedAt') or now_iso()
    if 'stallSince' not in sched:
        sched['stallSince'] = None
    if 'lastDispatchStatus' not in sched:
        sched['lastDispatchStatus'] = 'idle'
    if 'snapshot' not in sched:
        sched['snapshot'] = {
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'now': task.get('now', ''),
            'savedAt': now_iso(),
            'note': 'init',
        }
    return sched


def _scheduler_add_flow(task, remark, to=''):
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '太子调度',
        'to': to or task.get('org', ''),
        'remark': f'🧭 {remark}'
    })


def _scheduler_snapshot(task, note=''):
    sched = _ensure_scheduler(task)
    sched['snapshot'] = {
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'now': task.get('now', ''),
        'savedAt': now_iso(),
        'note': note or 'snapshot',
    }


def _scheduler_mark_progress(task, note=''):
    sched = _ensure_scheduler(task)
    sched['lastProgressAt'] = now_iso()
    sched['stallSince'] = None
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['lastEscalatedAt'] = None
    if note:
        _scheduler_add_flow(task, f'进展确认：{note}')


def _update_task_scheduler(task_id, updater):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return False
    sched = _ensure_scheduler(task)
    updater(task, sched)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return True


def get_scheduler_state(task_id):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'scheduler': sched,
        'stalledSec': stalled_sec,
        'checkedAt': now_iso(),
    }


def handle_scheduler_retry(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    sched = _ensure_scheduler(task)
    sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
    sched['lastRetryAt'] = now_iso()
    sched['lastDispatchTrigger'] = 'taizi-retry'
    _scheduler_add_flow(task, f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    dispatch_for_state(task_id, task, state, trigger='taizi-retry')
    return {'ok': True, 'message': f'{task_id} 已触发重试派发', 'retryCount': sched['retryCount']}


def handle_scheduler_escalate(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = _ensure_scheduler(task)
    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = now_iso()
    _scheduler_add_flow(task, f'升级到{target_label}协调：{reason or "任务停滞"}', to=target_label)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    wake_agent(target, msg)

    return {'ok': True, 'message': f'{task_id} 已升级至{target_label}', 'escalationLevel': next_level}


def handle_scheduler_rollback(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    old_state = task.get('state', '')
    task['state'] = snap_state
    task['org'] = snapshot.get('org', task.get('org', ''))
    task['now'] = f'↩️ 太子调度自动回滚：{reason or "恢复到上个稳定节点"}'
    task['block'] = '无'
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['stallSince'] = None
    sched['lastProgressAt'] = now_iso()
    _scheduler_add_flow(task, f'执行回滚：{old_state} → {snap_state}，原因：{reason or "停滞恢复"}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    if snap_state not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, snap_state, trigger='taizi-rollback')

    return {'ok': True, 'message': f'{task_id} 已回滚到 {snap_state}'}


def handle_scheduler_scan(threshold_sec=180):
    threshold_sec = max(30, int(threshold_sec or 180))
    tasks = load_tasks()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    pending_retries = []
    pending_escalates = []
    pending_rollbacks = []
    actions = []
    changed = False

    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        if state == 'Blocked':
            continue

        sched = _ensure_scheduler(task)
        task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
        last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
        if not last_progress:
            continue
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
        if stalled_sec < task_threshold:
            continue

        if not sched.get('stallSince'):
            sched['stallSince'] = now_iso()
            changed = True

        retry_count = int(sched.get('retryCount') or 0)
        max_retry = max(0, int(sched.get('maxRetry') or 1))
        level = int(sched.get('escalationLevel') or 0)

        if retry_count < max_retry:
            sched['retryCount'] = retry_count + 1
            sched['lastRetryAt'] = now_iso()
            sched['lastDispatchTrigger'] = 'taizi-scan-retry'
            _scheduler_add_flow(task, f'停滞{stalled_sec}秒，触发自动重试第{sched["retryCount"]}次')
            pending_retries.append((task_id, state))
            actions.append({'taskId': task_id, 'action': 'retry', 'stalledSec': stalled_sec})
            changed = True
            continue

        if level < 2:
            next_level = level + 1
            target = 'menxia' if next_level == 1 else 'shangshu'
            target_label = '门下省' if next_level == 1 else '尚书省'
            sched['escalationLevel'] = next_level
            sched['lastEscalatedAt'] = now_iso()
            _scheduler_add_flow(task, f'停滞{stalled_sec}秒，升级至{target_label}协调', to=target_label)
            pending_escalates.append((task_id, state, target, target_label, stalled_sec))
            actions.append({'taskId': task_id, 'action': 'escalate', 'to': target_label, 'stalledSec': stalled_sec})
            changed = True
            continue

        if sched.get('autoRollback', True):
            snapshot = sched.get('snapshot') or {}
            snap_state = snapshot.get('state')
            if snap_state and snap_state != state:
                old_state = state
                task['state'] = snap_state
                task['org'] = snapshot.get('org', task.get('org', ''))
                task['now'] = '↩️ 太子调度自动回滚到稳定节点'
                task['block'] = '无'
                sched['retryCount'] = 0
                sched['escalationLevel'] = 0
                sched['stallSince'] = None
                sched['lastProgressAt'] = now_iso()
                _scheduler_add_flow(task, f'连续停滞，自动回滚：{old_state} → {snap_state}')
                pending_rollbacks.append((task_id, snap_state))
                actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                changed = True

    if changed:
        save_tasks(tasks)

    for task_id, state in pending_retries:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task:
            dispatch_for_state(task_id, retry_task, state, trigger='taizi-scan-retry')

    for task_id, state, target, target_label, stalled_sec in pending_escalates:
        msg = (
            f'🧭 太子调度升级通知\n'
            f'任务ID: {task_id}\n'
            f'当前状态: {state}\n'
            f'已停滞: {stalled_sec} 秒\n'
            f'请立即介入协调推进\n'
            f'⚠️ 看板已有任务，请勿重复创建。'
        )
        wake_agent(target, msg)

    for task_id, state in pending_rollbacks:
        rollback_task = next((t for t in tasks if t.get('id') == task_id), None)
        if rollback_task and state not in _TERMINAL_STATES:
            dispatch_for_state(task_id, rollback_task, state, trigger='taizi-auto-rollback')

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'count': len(actions),
        'checkedAt': now_iso(),
    }


def _startup_recover_queued_dispatches():
    """服务启动后扫描 lastDispatchStatus=queued 的任务，重新派发。
    解决：kill -9 重启导致派发线程中断、任务永久卡住的问题。"""
    tasks = load_tasks()
    recovered = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') == 'queued':
            log.info(f'🔄 启动恢复: {task_id} 状态={state} 上次派发未完成，重新派发')
            sched['lastDispatchTrigger'] = 'startup-recovery'
            dispatch_for_state(task_id, task, state, trigger='startup-recovery')
            recovered += 1
    if recovered:
        log.info(f'✅ 启动恢复完成: 重新派发 {recovered} 个任务')
    else:
        log.info(f'✅ 启动恢复: 无需恢复')


def handle_repair_flow_order():
    """修复历史任务中首条流转为“皇上->中书省”的错序问题。"""
    tasks = load_tasks()
    fixed = 0
    fixed_ids = []

    for task in tasks:
        task_id = task.get('id', '')
        if not task_id.startswith('JJC-'):
            continue
        flow_log = task.get('flow_log') or []
        if not flow_log:
            continue

        first = flow_log[0]
        if first.get('from') != '皇上' or first.get('to') != '中书省':
            continue

        first['to'] = '太子'
        remark = first.get('remark', '')
        if isinstance(remark, str) and remark.startswith('下旨：'):
            first['remark'] = remark

        if task.get('state') == 'Zhongshu' and task.get('org') == '中书省' and len(flow_log) == 1:
            task['state'] = 'Taizi'
            task['org'] = '太子'
            task['now'] = '等待太子接旨分拣'

        task['updatedAt'] = now_iso()
        fixed += 1
        fixed_ids.append(task_id)

    if fixed:
        save_tasks(tasks)

    return {
        'ok': True,
        'count': fixed,
        'taskIds': fixed_ids[:80],
        'more': max(0, fixed - 80),
        'checkedAt': now_iso(),
    }


def _collect_message_text(msg):
    """收集消息中的可检索文本，用于 task_id/关键词过滤。"""
    parts = []
    for c in msg.get('content', []) or []:
        ctype = c.get('type')
        if ctype == 'text' and c.get('text'):
            parts.append(str(c.get('text', '')))
        elif ctype == 'thinking' and c.get('thinking'):
            parts.append(str(c.get('thinking', '')))
        elif ctype == 'tool_use':
            parts.append(json.dumps(c.get('input', {}), ensure_ascii=False))
    details = msg.get('details') or {}
    for key in ('output', 'stdout', 'stderr', 'message'):
        val = details.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return ''.join(parts)


def _parse_activity_entry(item):
    """将 session jsonl 的 message 统一解析成看板活动条目。"""
    msg = item.get('message') or {}
    role = str(msg.get('role', '')).strip().lower()
    ts = item.get('timestamp', '')

    if role == 'assistant':
        text = ''
        thinking = ''
        tool_calls = []
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text') and not text:
                text = str(c.get('text', '')).strip()
            elif c.get('type') == 'thinking' and c.get('thinking') and not thinking:
                thinking = str(c.get('thinking', '')).strip()[:200]
            elif c.get('type') == 'tool_use':
                tool_calls.append({
                    'name': c.get('name', ''),
                    'input_preview': json.dumps(c.get('input', {}), ensure_ascii=False)[:100]
                })
        if not (text or thinking or tool_calls):
            return None
        entry = {'at': ts, 'kind': 'assistant'}
        if text:
            entry['text'] = text[:300]
        if thinking:
            entry['thinking'] = thinking
        if tool_calls:
            entry['tools'] = tool_calls
        return entry

    if role in ('toolresult', 'tool_result'):
        details = msg.get('details') or {}
        code = details.get('exitCode')
        if code is None:
            code = details.get('code', details.get('status'))
        output = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                output = str(c.get('text', '')).strip()[:200]
                break
        if not output:
            for key in ('output', 'stdout', 'stderr', 'message'):
                val = details.get(key)
                if isinstance(val, str) and val.strip():
                    output = val.strip()[:200]
                    break

        entry = {
            'at': ts,
            'kind': 'tool_result',
            'tool': msg.get('toolName', msg.get('name', '')),
            'exitCode': code,
            'output': output,
        }
        duration_ms = details.get('durationMs')
        if isinstance(duration_ms, (int, float)):
            entry['durationMs'] = int(duration_ms)
        return entry

    if role == 'user':
        text = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                text = str(c.get('text', '')).strip()
                break
        if not text:
            return None
        return {'at': ts, 'kind': 'user', 'text': text[:200]}

    return None


def get_agent_activity(agent_id, limit=30, task_id=None):
    """从 Agent 的 session jsonl 读取最近活动。
    如果 task_id 不为空，只返回提及该 task_id 的相关条目。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    # 扫描所有 jsonl（按修改时间倒序），优先最新
    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    entries = []
    # 如果需要按 task_id 过滤，可能需要扫描多个文件
    files_to_scan = jsonl_files[:3] if task_id else jsonl_files[:1]

    for session_file in files_to_scan:
        try:
            lines = session_file.read_text(errors='ignore').splitlines()
        except Exception:
            continue

        # 正向扫描以保持时间顺序；如果有 task_id，收集提及 task_id 的条目
        for ln in lines:
            try:
                item = json.loads(ln)
            except Exception:
                continue
            msg = item.get('message') or {}
            all_text = _collect_message_text(msg)

            # task_id 过滤：只保留提及 task_id 的条目
            if task_id and task_id not in all_text:
                continue
            entry = _parse_activity_entry(item)
            if entry:
                entries.append(entry)

            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    # 只保留最后 limit 条
    return entries[-limit:]


def _extract_keywords(title):
    """从任务标题中提取有意义的关键词（用于 session 内容匹配）。"""
    stop = {'的', '了', '在', '是', '有', '和', '与', '或', '一个', '一篇', '关于', '进行',
            '写', '做', '请', '把', '给', '用', '要', '需要', '面向', '风格', '包含',
            '出', '个', '不', '可以', '应该', '如何', '怎么', '什么', '这个', '那个'}
    # 提取英文词
    en_words = re.findall(r'[a-zA-Z][\w.-]{1,}', title)
    # 提取 2-4 字中文词组（更短的颗粒度）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', title)
    all_words = en_words + cn_words
    kws = [w for w in all_words if w not in stop and len(w) >= 2]
    # 去重保序
    seen = set()
    unique = []
    for w in kws:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique.append(w)
    return unique[:8]  # 最多 8 个关键词


def get_agent_activity_by_keywords(agent_id, keywords, limit=20):
    """从 agent session 中按关键词匹配获取活动条目。
    找到包含关键词的 session 文件，只读该文件的活动。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 找到包含关键词的 session 文件
    target_file = None
    for sf in jsonl_files[:5]:
        try:
            content = sf.read_text(errors='ignore')
        except Exception:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in content.lower())
        if hits >= min(2, len(keywords)):
            target_file = sf
            break

    if not target_file:
        return []

    # 解析 session 文件，按 user 消息分割为对话段
    # 找到包含关键词的对话段，只返回该段的活动
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 第一遍：找到关键词匹配的 user 消息位置
    user_msg_indices = []  # (line_index, user_text)
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            text = ''
            for c in msg.get('content', []):
                if c.get('type') == 'text' and c.get('text'):
                    text += c['text']
            user_msg_indices.append((i, text))

    # 找到与关键词匹配度最高的 user 消息
    best_idx = -1
    best_hits = 0
    for line_idx, utext in user_msg_indices:
        hits = sum(1 for kw in keywords if kw.lower() in utext.lower())
        if hits > best_hits:
            best_hits = hits
            best_idx = line_idx

    # 确定对话段的行范围：从匹配的 user 消息到下一个 user 消息之前
    if best_idx >= 0 and best_hits >= min(2, len(keywords)):
        # 找下一个 user 消息的位置
        next_user_idx = len(lines)
        for line_idx, _ in user_msg_indices:
            if line_idx > best_idx:
                next_user_idx = line_idx
                break
        start_line = best_idx
        end_line = next_user_idx
    else:
        # 没找到匹配的对话段，返回空
        return []

    # 第二遍：只解析对话段内的行
    entries = []
    for ln in lines[start_line:end_line]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def get_agent_latest_segment(agent_id, limit=20):
    """获取 Agent 最新一轮对话段（最后一条 user 消息起的所有内容）。
    用于活跃任务没有精确匹配时，展示 Agent 的实时工作状态。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 读取最新的 session 文件
    target_file = jsonl_files[0]
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 找到最后一条 user 消息的行号
    last_user_idx = -1
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            last_user_idx = i

    if last_user_idx < 0:
        return []

    # 从最后一条 user 消息开始，解析到文件末尾
    entries = []
    for ln in lines[last_user_idx:]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def _compute_phase_durations(flow_log):
    """从 flow_log 计算每个阶段的停留时长。"""
    if not flow_log or len(flow_log) < 1:
        return []
    phases = []
    for i, fl in enumerate(flow_log):
        start_at = fl.get('at', '')
        to_dept = fl.get('to', '')
        remark = fl.get('remark', '')
        # 下一阶段的起始时间就是本阶段的结束时间
        if i + 1 < len(flow_log):
            end_at = flow_log[i + 1].get('at', '')
            ongoing = False
        else:
            end_at = now_iso()
            ongoing = True
        # 计算时长
        dur_sec = 0
        try:
            from_dt = datetime.datetime.fromisoformat(start_at.replace('Z', '+00:00'))
            to_dt = datetime.datetime.fromisoformat(end_at.replace('Z', '+00:00'))
            dur_sec = max(0, int((to_dt - from_dt).total_seconds()))
        except Exception:
            pass
        # 人类可读时长
        if dur_sec < 60:
            dur_text = f'{dur_sec}秒'
        elif dur_sec < 3600:
            dur_text = f'{dur_sec // 60}分{dur_sec % 60}秒'
        elif dur_sec < 86400:
            h, rem = divmod(dur_sec, 3600)
            dur_text = f'{h}小时{rem // 60}分'
        else:
            d, rem = divmod(dur_sec, 86400)
            dur_text = f'{d}天{rem // 3600}小时'
        phases.append({
            'phase': to_dept,
            'from': start_at,
            'to': end_at,
            'durationSec': dur_sec,
            'durationText': dur_text,
            'ongoing': ongoing,
            'remark': remark,
        })
    return phases


def _compute_todos_summary(todos):
    """计算 todos 完成率汇总。"""
    if not todos:
        return None
    total = len(todos)
    completed = sum(1 for t in todos if t.get('status') == 'completed')
    in_progress = sum(1 for t in todos if t.get('status') == 'in-progress')
    not_started = total - completed - in_progress
    percent = round(completed / total * 100) if total else 0
    return {
        'total': total,
        'completed': completed,
        'inProgress': in_progress,
        'notStarted': not_started,
        'percent': percent,
    }


def _compute_todos_diff(prev_todos, curr_todos):
    """计算两个 todos 快照之间的差异。"""
    prev_map = {str(t.get('id', '')): t for t in (prev_todos or [])}
    curr_map = {str(t.get('id', '')): t for t in (curr_todos or [])}
    changed, added, removed = [], [], []
    for tid, ct in curr_map.items():
        if tid in prev_map:
            pt = prev_map[tid]
            if pt.get('status') != ct.get('status'):
                changed.append({
                    'id': tid, 'title': ct.get('title', ''),
                    'from': pt.get('status', ''), 'to': ct.get('status', ''),
                })
        else:
            added.append({'id': tid, 'title': ct.get('title', '')})
    for tid, pt in prev_map.items():
        if tid not in curr_map:
            removed.append({'id': tid, 'title': pt.get('title', '')})
    if not changed and not added and not removed:
        return None
    return {'changed': changed, 'added': added, 'removed': removed}


def get_task_activity(task_id):
    """获取任务的实时进展数据。
    数据来源：
    1. 任务自身的 now / todos / flow_log 字段（由 Agent 通过 progress 命令主动上报）
    2. Agent session JSONL 中的对话日志（thinking / tool_result / user，用于展示思考过程）

    增强字段:
    - taskMeta: 任务元信息 (title/state/org/output/block/priority/reviewRound/archived)
    - phaseDurations: 各阶段停留时长
    - todosSummary: todos 完成率汇总
    - resourceSummary: Agent 资源消耗汇总 (tokens/cost/elapsed)
    - activity 条目中 progress/todos 保留 state/org 快照
    - activity 中 todos 条目含 diff 字段
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    state = task.get('state', '')
    org = task.get('org', '')
    now_text = task.get('now', '')
    todos = task.get('todos', [])
    updated_at = task.get('updatedAt', '')

    # ── 任务元信息 ──
    task_meta = {
        'title': task.get('title', ''),
        'state': state,
        'org': org,
        'output': task.get('output', ''),
        'block': task.get('block', ''),
        'priority': task.get('priority', 'normal'),
        'reviewRound': task.get('review_round', 0),
        'archived': task.get('archived', False),
    }

    # 当前负责 Agent（兼容旧逻辑）
    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        agent_id = _ORG_AGENT_MAP.get(org)

    # ── 构建活动条目列表（flow_log + progress_log）──
    activity = []
    flow_log = task.get('flow_log', [])

    # 1. flow_log 转为活动条目
    for fl in flow_log:
        activity.append({
            'at': fl.get('at', ''),
            'kind': 'flow',
            'from': fl.get('from', ''),
            'to': fl.get('to', ''),
            'remark': fl.get('remark', ''),
        })

    progress_log = task.get('progress_log', [])
    related_agents = set()

    # 资源消耗累加
    total_tokens = 0
    total_cost = 0.0
    total_elapsed = 0
    has_resource_data = False

    # 用于 todos diff 计算
    prev_todos_snapshot = None

    if progress_log:
        # 2. 多 Agent 实时进展日志（每条 progress 都保留自己的 todo 快照）
        for pl in progress_log:
            p_at = pl.get('at', '')
            p_agent = pl.get('agent', '')
            p_text = pl.get('text', '')
            p_todos = pl.get('todos', [])
            p_state = pl.get('state', '')
            p_org = pl.get('org', '')
            if p_agent:
                related_agents.add(p_agent)
            # 累加资源消耗
            if pl.get('tokens'):
                total_tokens += pl['tokens']
                has_resource_data = True
            if pl.get('cost'):
                total_cost += pl['cost']
                has_resource_data = True
            if pl.get('elapsed'):
                total_elapsed += pl['elapsed']
                has_resource_data = True
            if p_text:
                entry = {
                    'at': p_at,
                    'kind': 'progress',
                    'text': p_text,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 单条资源数据
                if pl.get('tokens'):
                    entry['tokens'] = pl['tokens']
                if pl.get('cost'):
                    entry['cost'] = pl['cost']
                if pl.get('elapsed'):
                    entry['elapsed'] = pl['elapsed']
                activity.append(entry)
            if p_todos:
                todos_entry = {
                    'at': p_at,
                    'kind': 'todos',
                    'items': p_todos,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 计算 diff
                diff = _compute_todos_diff(prev_todos_snapshot, p_todos)
                if diff:
                    todos_entry['diff'] = diff
                activity.append(todos_entry)
                prev_todos_snapshot = p_todos

        # 仅当无法通过状态确定 Agent 时，才回退到最后一次上报的 Agent
        if not agent_id:
            last_pl = progress_log[-1]
            if last_pl.get('agent'):
                agent_id = last_pl.get('agent')
    else:
        # 兼容旧数据：仅使用 now/todos
        if now_text:
            activity.append({
                'at': updated_at,
                'kind': 'progress',
                'text': now_text,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })
        if todos:
            activity.append({
                'at': updated_at,
                'kind': 'todos',
                'items': todos,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })

    # 按时间排序，保证流转/进展穿插正确
    activity.sort(key=lambda x: x.get('at', ''))

    if agent_id:
        related_agents.add(agent_id)

    # ── 融合 Agent Session 活动（thinking / tool_result / user）──
    # 从 session JSONL 中提取 Agent 的思考过程和工具调用记录
    try:
        session_entries = []
        # 活跃任务：尝试按 task_id 精确匹配
        if state not in ('Done', 'Cancelled'):
            if agent_id:
                entries = get_agent_activity(agent_id, limit=30, task_id=task_id)
                session_entries.extend(entries)
            # 也从其他相关 Agent 获取
            for ra in related_agents:
                if ra != agent_id:
                    entries = get_agent_activity(ra, limit=20, task_id=task_id)
                    session_entries.extend(entries)
        else:
            # 已完成任务：基于关键词匹配
            title = task.get('title', '')
            keywords = _extract_keywords(title)
            if keywords:
                agents_to_scan = list(related_agents) if related_agents else ([agent_id] if agent_id else [])
                for ra in agents_to_scan[:5]:
                    entries = get_agent_activity_by_keywords(ra, keywords, limit=15)
                    session_entries.extend(entries)
        # 去重（通过 at+kind 去重避免重复）
        existing_keys = {(a.get('at', ''), a.get('kind', '')) for a in activity}
        for se in session_entries:
            key = (se.get('at', ''), se.get('kind', ''))
            if key not in existing_keys:
                activity.append(se)
                existing_keys.add(key)
        # 重新排序
        activity.sort(key=lambda x: x.get('at', ''))
    except Exception as e:
        log.warning(f'Session JSONL 融合失败 (task={task_id}): {e}')

    # ── 阶段耗时统计 ──
    phase_durations = _compute_phase_durations(flow_log)

    # ── Todos 汇总 ──
    todos_summary = _compute_todos_summary(todos)

    # ── 总耗时（首条 flow_log 到最后一条/当前） ──
    total_duration = None
    if flow_log:
        try:
            first_at = datetime.datetime.fromisoformat(flow_log[0].get('at', '').replace('Z', '+00:00'))
            if state in ('Done', 'Cancelled') and len(flow_log) >= 2:
                last_at = datetime.datetime.fromisoformat(flow_log[-1].get('at', '').replace('Z', '+00:00'))
            else:
                last_at = datetime.datetime.now(datetime.timezone.utc)
            dur = max(0, int((last_at - first_at).total_seconds()))
            if dur < 60:
                total_duration = f'{dur}秒'
            elif dur < 3600:
                total_duration = f'{dur // 60}分{dur % 60}秒'
            elif dur < 86400:
                h, rem = divmod(dur, 3600)
                total_duration = f'{h}小时{rem // 60}分'
            else:
                d, rem = divmod(dur, 86400)
                total_duration = f'{d}天{rem // 3600}小时'
        except Exception:
            pass

    result = {
        'ok': True,
        'taskId': task_id,
        'taskMeta': task_meta,
        'agentId': agent_id,
        'agentLabel': _STATE_LABELS.get(state, state),
        'lastActive': updated_at[:19].replace('T', ' ') if updated_at else None,
        'activity': activity,
        'activitySource': 'progress+session',
        'relatedAgents': sorted(list(related_agents)),
        'phaseDurations': phase_durations,
        'totalDuration': total_duration,
    }
    if todos_summary:
        result['todosSummary'] = todos_summary
    if has_resource_data:
        result['resourceSummary'] = {
            'totalTokens': total_tokens,
            'totalCost': round(total_cost, 4),
            'totalElapsedSec': total_elapsed,
        }
    return result


# 状态推进顺序（手动推进用）
_STATE_FLOW = {
    'Pending':  ('Taizi', '皇上', '太子', '待处理旨意转交太子分拣'),
    'Taizi':    ('Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'),
    'Zhongshu': ('Menxia', '中书省', '门下省', '中书省方案提交门下省审议'),
    'Menxia':   ('Assigned', '门下省', '尚书省', '门下省准奏，转尚书省派发'),
    'Assigned': ('Doing', '尚书省', '六部', '尚书省开始派发执行'),
    'Next':     ('Doing', '尚书省', '六部', '待执行任务开始执行'),
    'Doing':    ('Review', '六部', '尚书省', '各部完成，进入汇总'),
    'Review':   ('Done', '尚书省', '太子', '全流程完成，回奏太子转报皇上'),
}
_STATE_LABELS = {
    'Pending': '待处理', 'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
    'Assigned': '尚书省', 'Next': '待执行', 'Doing': '执行中', 'Review': '审查', 'Done': '完成',
}


def dispatch_for_state(task_id, task, new_state, trigger='state-transition'):
    """推进/审批后自动派发对应 Agent（后台异步，不阻塞响应）。"""
    agent_id = _STATE_AGENT_MAP.get(new_state)
    if agent_id is None and new_state in ('Doing', 'Next'):
        org = task.get('org', '')
        agent_id = _ORG_AGENT_MAP.get(org)
    if not agent_id:
        log.info(f'ℹ️ {task_id} 新状态 {new_state} 无对应 Agent，跳过自动派发')
        return

    _update_task_scheduler(task_id, lambda t, s: (
        s.update({
            'lastDispatchAt': now_iso(),
            'lastDispatchStatus': 'queued',
            'lastDispatchAgent': agent_id,
            'lastDispatchTrigger': trigger,
        }),
        _scheduler_add_flow(t, f'已入队派发：{new_state} → {agent_id}（{trigger}）', to=_STATE_LABELS.get(new_state, new_state))
    ))

    title = task.get('title', '(无标题)')
    target_dept = task.get('targetDept', '')

    # 根据 agent_id 构造针对性消息
    _msgs = {
        'taizi': (
            f'📜 皇上旨意需要你处理\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。\n'
            f'请立即转交中书省起草执行方案。'
        ),
        'zhongshu': (
            f'📜 旨意已到中书省，请起草方案\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务记录，请勿重复创建。直接用 kanban_update.py state 更新状态。\n'
            f'请立即起草执行方案，走完完整三省流程（中书起草→门下审议→尚书派发→六部执行）。'
        ),
        'menxia': (
            f'📋 中书省方案提交审议\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'请审议中书省方案，给出准奏或封驳意见。'
        ),
        'shangshu': (
            f'📮 门下省已准奏，请派发执行\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'{"建议派发部门: " + target_dept if target_dept else ""}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'请分析方案并派发给六部执行。'
        ),
    }
    msg = _msgs.get(agent_id, (
        f'📌 请处理任务\n'
        f'任务ID: {task_id}\n'
        f'旨意: {title}\n'
        f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。'
    ))

    def _do_dispatch():
        try:
            if not _check_gateway_alive():
                log.warning(f'⚠️ {task_id} 自动派发跳过: Gateway 未启动')
                _update_task_scheduler(task_id, lambda t, s: s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'gateway-offline',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                }))
                return
            cmd = ['openclaw', 'agent', '--agent', agent_id, '-m', msg,
                   '--deliver', '--channel', 'feishu', '--timeout', '300']
            max_retries = 2
            err = ''
            for attempt in range(1, max_retries + 1):
                log.info(f'🔄 自动派发 {task_id} → {agent_id} (第{attempt}次)...')
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=310)
                if result.returncode == 0:
                    log.info(f'✅ {task_id} 自动派发成功 → {agent_id}')
                    _update_task_scheduler(task_id, lambda t, s: (
                        s.update({
                            'lastDispatchAt': now_iso(),
                            'lastDispatchStatus': 'success',
                            'lastDispatchAgent': agent_id,
                            'lastDispatchTrigger': trigger,
                            'lastDispatchError': '',
                        }),
                        _scheduler_add_flow(t, f'派发成功：{agent_id}（{trigger}）', to=t.get('org', ''))
                    ))
                    return
                err = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {task_id} 自动派发失败(第{attempt}次): {err}')
                if attempt < max_retries:
                    import time
                    time.sleep(5)
            log.error(f'❌ {task_id} 自动派发最终失败 → {agent_id}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'failed',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': err,
                }),
                _scheduler_add_flow(t, f'派发失败：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))
        except subprocess.TimeoutExpired:
            log.error(f'❌ {task_id} 自动派发超时 → {agent_id}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'timeout',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': 'timeout',
                }),
                _scheduler_add_flow(t, f'派发超时：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))
        except Exception as e:
            log.warning(f'⚠️ {task_id} 自动派发异常: {e}')
            _update_task_scheduler(task_id, lambda t, s: (
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'error',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': str(e)[:200],
                }),
                _scheduler_add_flow(t, f'派发异常：{agent_id}（{trigger}）', to=t.get('org', ''))
            ))

    threading.Thread(target=_do_dispatch, daemon=True).start()
    log.info(f'🚀 {task_id} 推进后自动派发 → {agent_id}')


def handle_advance_state(task_id, comment=''):
    """手动推进任务到下一阶段（解卡用），推进后自动派发对应 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    cur = task.get('state', '')
    if cur not in _STATE_FLOW:
        return {'ok': False, 'error': f'任务 {task_id} 状态为 {cur}，无法推进'}
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'advance-before-{cur}')
    next_state, from_dept, to_dept, default_remark = _STATE_FLOW[cur]
    remark = comment or default_remark

    task['state'] = next_state
    task['now'] = f'⬇️ 手动推进：{remark}'
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': from_dept,
        'to': to_dept,
        'remark': f'⬇️ 手动推进：{remark}'
    })
    _scheduler_mark_progress(task, f'手动推进 {cur} -> {next_state}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    # 🚀 推进后自动派发对应 Agent（Done 状态无需派发）
    if next_state != 'Done':
        dispatch_for_state(task_id, task, next_state)

    from_label = _STATE_LABELS.get(cur, cur)
    to_label = _STATE_LABELS.get(next_state, next_state)
    dispatched = ' (已自动派发 Agent)' if next_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {from_label} → {to_label}{dispatched}'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 只记录 4xx/5xx 错误请求
        if args and len(args) >= 1:
            status = str(args[0]) if args else ''
            if status.startswith('4') or status.startswith('5'):
                log.warning(f'{self.client_address[0]} {fmt % args}')

    def handle_error(self):
        pass  # 静默处理连接错误，避免 BrokenPipe 崩溃

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开连接，忽略

    def do_OPTIONS(self):
        self.send_response(200)
        cors_headers(self)
        self.end_headers()

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, path: pathlib.Path, mime='text/html; charset=utf-8'):
        if not path.exists():
            self.send_error(404)
            return
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, rel_path):
        """从 dist/ 目录提供静态文件。"""
        safe = rel_path.replace('\\', '/').lstrip('/')
        if '..' in safe:
            self.send_error(403)
            return True
        fp = DIST / safe
        if fp.is_file():
            mime = _MIME_TYPES.get(fp.suffix.lower(), 'application/octet-stream')
            self.send_file(fp, mime)
            return True
        return False

    def do_GET(self):
        p = urlparse(self.path).path.rstrip('/')
        if p in ('', '/dashboard', '/dashboard.html'):
            self.send_file(DIST / 'index.html')
        elif p == '/healthz':
            checks = {'dataDir': DATA.is_dir(), 'tasksReadable': (DATA / 'tasks_source.json').exists()}
            checks['dataWritable'] = os.access(str(DATA), os.W_OK)
            all_ok = all(checks.values())
            self.send_json({'status': 'ok' if all_ok else 'degraded', 'ts': now_iso(), 'checks': checks})
        elif p == '/api/live-status':
            self.send_json(read_json(DATA / 'live_status.json'))
        elif p == '/api/agent-config':
            self.send_json(read_json(DATA / 'agent_config.json'))
        elif p == '/api/model-change-log':
            self.send_json(read_json(DATA / 'model_change_log.json', []))
        elif p == '/api/last-result':
            self.send_json(read_json(DATA / 'last_model_change_result.json', {}))
        elif p == '/api/officials-stats':
            self.send_json(read_json(DATA / 'officials_stats.json', {}))
        elif p == '/api/morning-brief':
            self.send_json(read_json(DATA / 'morning_brief.json', {}))
        elif p == '/api/morning-config':
            self.send_json(read_json(DATA / 'morning_brief_config.json', {
                'categories': [
                    {'name': '政治', 'enabled': True},
                    {'name': '军事', 'enabled': True},
                    {'name': '经济', 'enabled': True},
                    {'name': 'AI大模型', 'enabled': True},
                ],
                'keywords': [], 'custom_feeds': [], 'feishu_webhook': '',
            }))
        elif p == '/api/telegram-config':
            # GET: 获取 Telegram 推送配置
            self.send_json(read_json(DATA / 'telegram_push_config.json', {
                'bot_token': '',
                'chat_id': '',
                'enabled': True
            }))
        elif p.startswith('/api/morning-brief/'):
            date = p.split('/')[-1]
            # 标准化日期格式为 YYYYMMDD（兼容 YYYY-MM-DD 输入）
            date_clean = date.replace('-', '')
            if not date_clean.isdigit() or len(date_clean) != 8:
                self.send_json({'ok': False, 'error': f'日期格式无效: {date}，请使用 YYYYMMDD'}, 400)
                return
            self.send_json(read_json(DATA / f'morning_brief_{date_clean}.json', {}))
        elif p == '/api/remote-skills-list':
            self.send_json(get_remote_skills_list())
        elif p.startswith('/api/skill-content/'):
            # /api/skill-content/{agentId}/{skillName}
            parts = p.replace('/api/skill-content/', '').split('/', 1)
            if len(parts) == 2:
                self.send_json(read_skill_content(parts[0], parts[1]))
            else:
                self.send_json({'ok': False, 'error': 'Usage: /api/skill-content/{agentId}/{skillName}'}, 400)
        elif p.startswith('/api/task-activity/'):
            task_id = p.replace('/api/task-activity/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_task_activity(task_id))
        elif p.startswith('/api/scheduler-state/'):
            task_id = p.replace('/api/scheduler-state/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_state(task_id))
        elif p == '/api/usage-cost':
            # 独立计算用量（从各agent的sessions.json聚合）
            try:
                from usage import get_usage_cost
                data = get_usage_cost()
                self.send_json(data)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
        elif p == '/api/agents-status':
            _reload_agents_if_needed()  # 检查文件是否变化
            self.send_json(get_agents_status())
        # workspace文件API - 获取工作区的markdown文件
        elif p.startswith('/api/workspace-files'):
            import re
            match = re.match(r'/api/workspace-files(?:/(\w+))?', p)
            agent_id = match.group(1) if match else 'main'
            # 构建工作区路径: main用workspace，子agent用workspace-{agent_id}
            if agent_id == 'main':
                ws_path = WORKSPACE_DIR
            else:
                ws_path = WORKSPACE_DIR.parent / f'workspace-{agent_id}'
            
            files_data = {}
            if ws_path.exists():
                # 读取主要的markdown文件
                key_files = ['IDENTITY.md', 'MEMORY.md', 'AGENTS.md', 'SOUL.md', 'TOOLS.md', 'USER.md', 'HEARTBEAT.md', 'BOOTSTRAP.md']
                for fname in key_files:
                    fpath = ws_path / fname
                    if fpath.exists():
                        try:
                            content = fpath.read_text(encoding='utf-8')
                            files_data[fname.replace('.md', '')] = content[:5000]  # 限制内容长度
                        except:
                            pass
            
            self.send_json({'ok': True, 'agent': agent_id, 'files': files_data})
        elif p == '/api/rankings':
            _reload_rankings_if_needed()  # 检查排位变化
            self.send_json({'ok': True, 'rankings': _get_rankings()})
        elif p == '/api/events':
            _init_events()  # 初始化事件
            _generate_events()  # 生成新事件
            
            # 读取本地任务事件文件（左边监督日志用）
            task_events = []
            try:
                import json
                task_file = pathlib.Path(__file__).parent / 'task_events.json'
                with open(task_file, 'r', encoding='utf-8') as f:
                    task_events = json.load(f)
            except Exception as e:
                pass
            
            # 返回事件数据
            self.send_json({'ok': True, 'events': _events, 'taskEvents': task_events})
        
        elif p.startswith('/api/add-task-event'):
            # 手动添加任务事件（子agent完成任务后调用）
            import json
            
            # 简单解析URL参数
            if '?' in p:
                query = p.split('?', 1)[1]
                params = {}
                for pair in query.split('&'):
                    if '=' in pair:
                        k, v = pair.split('=', 1)
                        params[k] = unquote(v)
            else:
                params = {}
            
            agent_id = params.get('agent', '总指挥')
            desc = params.get('desc', '任务完成')
            event_type = params.get('type', 'success')
            
            # 格式化描述：<agent> 完成：desc
            formatted_desc = f"< {agent_id} > 完成：{desc}"
            
            task_file = pathlib.Path(__file__).parent / 'task_events.json'
            try:
                events = json.loads(task_file.read_text(encoding='utf-8'))
            except:
                events = []
            
            now = datetime.datetime.now()
            events.insert(0, {
                "time": now.strftime("%H:%M"),
                "sort_key": now.isoformat(),
                "title": "任务完成",
                "desc": formatted_desc,
                "type": event_type
            })
            events = events[:30]
            task_file.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
            
            self.send_json({'ok': True, 'desc': desc})
            
            # 合并：原始事件 + 任务事件（任务事件放前面）
            all_events = task_events + _get_events()
            self.send_json({'ok': True, 'events': all_events[:30]})
        
        # ===== 三省六部任务API (GET) =====
        elif _task_service and p == '/api/tasks':
            # GET /api/tasks - 任务列表
            tasks = _task_service.list_tasks()
            self.send_json({'ok': True, 'tasks': [t.to_dict() for t in tasks]})
        
        elif _task_service and p == '/api/tasks/live-status':
            # GET /api/tasks/live-status - 实时状态
            status = _task_service.get_live_status()
            self.send_json({'ok': True, 'status': status})
        
        elif _task_service and p.startswith('/api/task/') and p != '/api/task/create':
            # GET /api/task/<id> - 获取单个任务
            task_id = p.replace('/api/task/', '')
            task = _task_service.get_task(task_id)
            if task:
                self.send_json({'ok': True, 'task': task.to_dict()})
            else:
                self.send_json({'ok': False, 'error': 'Task not found'}, 404)
        elif p.startswith('/api/agent-activity/'):
            agent_id = p.replace('/api/agent-activity/', '')
            if not agent_id or not _SAFE_NAME_RE.match(agent_id):
                self.send_json({'ok': False, 'error': 'invalid agent_id'}, 400)
            else:
                self.send_json({'ok': True, 'agentId': agent_id, 'activity': get_agent_activity(agent_id)})
        elif self._serve_static(p):
            pass  # 已由 _serve_static 处理 (JS/CSS/图片等)
        else:
            # SPA fallback：非 /api/ 路径返回 index.html
            if not p.startswith('/api/'):
                idx = DIST / 'index.html'
                if idx.exists():
                    self.send_file(idx)
                    return
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_REQUEST_BODY:
            self.send_json({'ok': False, 'error': f'Request body too large (max {MAX_REQUEST_BODY} bytes)'}, 413)
            return
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            self.send_json({'ok': False, 'error': 'invalid JSON'}, 400)
            return

        if p == '/api/morning-config':
            # 字段校验
            if not isinstance(body, dict):
                self.send_json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
                return
            allowed_keys = {'categories', 'keywords', 'custom_feeds', 'feishu_webhook'}
            unknown = set(body.keys()) - allowed_keys
            if unknown:
                self.send_json({'ok': False, 'error': f'未知字段: {", ".join(unknown)}'}, 400)
                return
            if 'categories' in body and not isinstance(body['categories'], list):
                self.send_json({'ok': False, 'error': 'categories 必须是数组'}, 400)
                return
            if 'keywords' in body and not isinstance(body['keywords'], list):
                self.send_json({'ok': False, 'error': 'keywords 必须是数组'}, 400)
                return
            # 飞书 Webhook 校验
            webhook = body.get('feishu_webhook', '').strip()
            if webhook and not validate_url(webhook, allowed_schemes=('https',), allowed_domains=('open.feishu.cn', 'open.larksuite.com')):
                self.send_json({'ok': False, 'error': '飞书 Webhook URL 无效，仅支持 https://open.feishu.cn 或 open.larksuite.com 域名'}, 400)
                return
            cfg_path = DATA / 'morning_brief_config.json'
            cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            self.send_json({'ok': True, 'message': '订阅配置已保存'})
            return

        if p == '/api/scheduler-scan':
            threshold_sec = body.get('thresholdSec', 180)
            try:
                result = handle_scheduler_scan(threshold_sec)
                self.send_json(result)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'scheduler scan failed: {e}'}, 500)
            return

        if p == '/api/repair-flow-order':
            try:
                self.send_json(handle_repair_flow_order())
            except Exception as e:
                self.send_json({'ok': False, 'error': f'repair flow order failed: {e}'}, 500)
            return

        if p == '/api/scheduler-retry':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_retry(task_id, reason))
            return

        if p == '/api/scheduler-escalate':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_escalate(task_id, reason))
            return

        if p == '/api/scheduler-rollback':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_rollback(task_id, reason))
            return

        if p == '/api/morning-brief/refresh':
            force = body.get('force', True)  # 从看板手动触发默认强制
            def do_refresh():
                try:
                    cmd = ['python3', str(SCRIPTS / 'fetch_morning_news.py')]
                    if force:
                        cmd.append('--force')
                    subprocess.run(cmd, timeout=120)
                    push_to_feishu()
                    push_to_telegram()  # 同时推送到 Telegram
                except Exception as e:
                    print(f'[refresh error] {e}', file=sys.stderr)
            threading.Thread(target=do_refresh, daemon=True).start()
            self.send_json({'ok': True, 'message': '采集已触发，约30-60秒后刷新'})
            return

        # ===== Telegram 推送配置 =====
        if p == '/api/telegram-config':
            # GET: 获取 Telegram 配置
            cfg = read_json(DATA / 'telegram_push_config.json', {
                'bot_token': '',
                'chat_id': '',
                'enabled': True
            })
            self.send_json({'ok': True, 'config': cfg})
            return

        if p == '/api/telegram-config/update':
            # POST: 更新 Telegram 配置
            bot_token = body.get('bot_token', '').strip()
            chat_id = body.get('chat_id', '').strip()
            enabled = body.get('enabled', True)
            
            if not bot_token:
                self.send_json({'ok': False, 'error': 'bot_token 不能为空'}, 400)
                return
            if not chat_id:
                self.send_json({'ok': False, 'error': 'chat_id 不能为空'}, 400)
                return
            
            cfg = {
                'bot_token': bot_token,
                'chat_id': chat_id,
                'enabled': enabled
            }
            
            cfg_path = DATA / 'telegram_push_config.json'
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
            self.send_json({'ok': True, 'message': 'Telegram 配置已更新', 'config': cfg})
            return

        if p == '/api/telegram-config/test':
            # POST: 测试 Telegram 配置
            result = test_telegram_config()
            self.send_json(result)
            return

        if p == '/api/telegram-push':
            # POST: 手动推送消息到 Telegram
            message = body.get('message', '').strip()
            result = push_to_telegram(message if message else None)
            self.send_json(result)
            return

        if p == '/api/add-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', body.get('name', '')).strip()
            desc = body.get('description', '').strip() or skill_name
            trigger = body.get('trigger', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = add_skill_to_agent(agent_id, skill_name, desc, trigger)
            self.send_json(result)
            return

        if p == '/api/add-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            source_url = body.get('sourceUrl', '').strip()
            description = body.get('description', '').strip()
            if not agent_id or not skill_name or not source_url:
                self.send_json({'ok': False, 'error': 'agentId, skillName, and sourceUrl required'}, 400)
                return
            result = add_remote_skill(agent_id, skill_name, source_url, description)
            self.send_json(result)
            return

        if p == '/api/remote-skills-list':
            result = get_remote_skills_list()
            self.send_json(result)
            return

        if p == '/api/update-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = update_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/remove-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = remove_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/task-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # stop, cancel, resume
            reason = body.get('reason', '').strip() or f'皇上从看板{action}'
            if not task_id or action not in ('stop', 'cancel', 'resume'):
                self.send_json({'ok': False, 'error': 'taskId and action(stop/cancel/resume) required'}, 400)
                return
            result = handle_task_action(task_id, action, reason)
            self.send_json(result)
            return

        if p == '/api/archive-task':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            archived = body.get('archived', True)
            archive_all = body.get('archiveAllDone', False)
            if not task_id and not archive_all:
                self.send_json({'ok': False, 'error': 'taskId or archiveAllDone required'}, 400)
                return
            result = handle_archive_task(task_id, archived, archive_all)
            self.send_json(result)
            return

        if p == '/api/task-todos':
            task_id = body.get('taskId', '').strip()
            todos = body.get('todos', [])  # [{id, title, status}]
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            # todos 输入校验
            if not isinstance(todos, list) or len(todos) > 200:
                self.send_json({'ok': False, 'error': 'todos must be a list (max 200 items)'}, 400)
                return
            valid_statuses = {'not-started', 'in-progress', 'completed'}
            for td in todos:
                if not isinstance(td, dict) or 'id' not in td or 'title' not in td:
                    self.send_json({'ok': False, 'error': 'each todo must have id and title'}, 400)
                    return
                if td.get('status', 'not-started') not in valid_statuses:
                    td['status'] = 'not-started'
            result = update_task_todos(task_id, todos)
            self.send_json(result)
            return

        if p == '/api/create-task':
            title = body.get('title', '').strip()
            org = body.get('org', '中书省').strip()
            official = body.get('official', '中书令').strip()
            priority = body.get('priority', 'normal').strip()
            template_id = body.get('templateId', '')
            params = body.get('params', {})
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            target_dept = body.get('targetDept', '').strip()
            result = handle_create_task(title, org, official, priority, template_id, params, target_dept)
            self.send_json(result)
            return

        if p == '/api/review-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # approve, reject
            comment = body.get('comment', '').strip()
            if not task_id or action not in ('approve', 'reject'):
                self.send_json({'ok': False, 'error': 'taskId and action(approve/reject) required'}, 400)
                return
            result = handle_review_action(task_id, action, comment)
            self.send_json(result)
            return

        if p == '/api/advance-state':
            task_id = body.get('taskId', '').strip()
            comment = body.get('comment', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_advance_state(task_id, comment)
            self.send_json(result)
            return

        if p == '/api/agent-wake':
            agent_id = body.get('agentId', '').strip()
            message = body.get('message', '').strip()
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId required'}, 400)
                return
            result = wake_agent(agent_id, message)
            self.send_json(result)
            return

        if p == '/api/set-model':
            agent_id = body.get('agentId', '').strip()
            model = body.get('model', '').strip()
            if not agent_id or not model:
                self.send_json({'ok': False, 'error': 'agentId and model required'}, 400)
                return

            # Write to pending (atomic)
            pending_path = DATA / 'pending_model_changes.json'
            def update_pending(current):
                current = [x for x in current if x.get('agentId') != agent_id]
                current.append({'agentId': agent_id, 'model': model})
                return current
            atomic_json_update(pending_path, update_pending, [])

            # Async apply
            def apply_async():
                try:
                    subprocess.run(['python3', str(SCRIPTS / 'apply_model_changes.py')], timeout=30)
                    subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
                except Exception as e:
                    print(f'[apply error] {e}', file=sys.stderr)

            threading.Thread(target=apply_async, daemon=True).start()
            self.send_json({'ok': True, 'message': f'Queued: {agent_id} → {model}'})
        
        # ===== 三省六部任务API =====
        elif _task_service and p == '/api/task/create':
            # POST /api/task/create - 创建任务
            title = body.get('title', '').strip()
            description = body.get('description', '')
            priority = body.get('priority', '中')
            assignee_org = body.get('assignee_org')
            
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            
            import asyncio
            task = asyncio.run(_task_service.create_task(
                title=title,
                description=description,
                priority=priority,
                assignee_org=assignee_org,
            ))
            self.send_json({'ok': True, 'task': task.to_dict()})
        
        elif _task_service and p.startswith('/api/task/') and p.endswith('/transition'):
            # POST /api/task/<id>/transition - 状态流转
            task_id = p.replace('/api/task/', '').replace('/transition', '')
            new_state = body.get('state', '').strip()
            reason = body.get('reason', '')
            
            if not new_state:
                self.send_json({'ok': False, 'error': 'state required'}, 400)
                return
            
            try:
                task_state = TaskState(new_state)
            except:
                self.send_json({'ok': False, 'error': f'Invalid state: {new_state}'}, 400)
                return
            
            import asyncio
            try:
                task = asyncio.run(_task_service.transition_state(task_id, task_state, reason=reason))
                self.send_json({'ok': True, 'task': task.to_dict()})
            except ValueError as e:
                self.send_json({'ok': False, 'error': str(e)}, 400)
        
        elif _task_service and p.startswith('/api/task/') and p.endswith('/dispatch'):
            # POST /api/task/<id>/dispatch - 派发到部门
            task_id = p.replace('/api/task/', '').replace('/dispatch', '')
            org = body.get('org', '').strip()
            
            if not org:
                self.send_json({'ok': False, 'error': 'org required'}, 400)
                return
            
            import asyncio
            try:
                task = asyncio.run(_task_service.dispatch_to_org(task_id, org))
                self.send_json({'ok': True, 'task': task.to_dict()})
            except ValueError as e:
                self.send_json({'ok': False, 'error': str(e)}, 400)
        
        elif _task_service and p == '/api/tasks':
            # GET /api/tasks - 任务列表
            state = body.get('state')
            assignee_org = body.get('assignee_org')
            priority = body.get('priority')
            
            if state:
                try:
                    state = TaskState(state)
                except:
                    state = None
            
            tasks = _task_service.list_tasks(
                state=state,
                assignee_org=assignee_org,
                priority=priority,
            )
            self.send_json({'ok': True, 'tasks': [t.to_dict() for t in tasks]})
        
        elif _task_service and p == '/api/tasks/live-status':
            # GET /api/tasks/live-status - 实时状态
            status = _task_service.get_live_status()
            self.send_json({'ok': True, 'status': status})
        
        elif _task_service and p.startswith('/api/task/') and p != '/api/task/create':
            # GET /api/task/<id> - 获取单个任务
            task_id = p.replace('/api/task/', '')
            task = _task_service.get_task(task_id)
            if task:
                self.send_json({'ok': True, 'task': task.to_dict()})
            else:
                self.send_json({'ok': False, 'error': 'Task not found'}, 404)
        
        elif p == '/api/log-event' and self.command == 'POST':
            # POST /api/log-event - 记录事件到监督日志
            # Body: {title, desc, type}
            title = body.get('title', '')
            desc = body.get('desc', '')
            event_type = body.get('type', 'info')
            
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            
            # 读取现有事件
            task_file = pathlib.Path(__file__).parent / 'task_events.json'
            try:
                events = json.loads(task_file.read_text(encoding='utf-8'))
            except:
                events = []
            
            now = datetime.datetime.now()
            events.insert(0, {
                "time": now.strftime("%H:%M"),
                "sort_key": now.isoformat(),
                "title": title,
                "desc": desc,
                "type": event_type
            })
            
            # 也添加到内存中的 _events（供 /api/events 使用）
            _events.insert(0, {
                "time": now.strftime("%H:%M"),
                "sort_key": now.isoformat(),
                "title": title,
                "desc": desc,
                "type": event_type
            })
            events = events[:30]
            task_file.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
            
            self.send_json({'ok': True})
        
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description='三省六部看板服务器')
    parser.add_argument('--port', type=int, default=7891)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--cors', default=None, help='Allowed CORS origin (default: reflect request Origin header)')
    args = parser.parse_args()

    global ALLOWED_ORIGIN
    ALLOWED_ORIGIN = args.cors

    server = HTTPServer((args.host, args.port), Handler)
    log.info(f'三省六部看板启动 → http://{args.host}:{args.port}')
    print(f'   按 Ctrl+C 停止')

    # 启动恢复：重新派发上次被 kill 中断的 queued 任务
    threading.Timer(3.0, _startup_recover_queued_dispatches).start()

    # 启动任务状态监控定时任务
    _start_task_monitor()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
