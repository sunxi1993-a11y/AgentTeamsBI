"""
三省六部任务系统 - 完全版
参考 edict-main 完整实现，去掉数据库依赖，使用JSON文件存储
"""
import json
import pathlib
import uuid
import enum
import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger('task_state')

# ===== 状态定义（完全复制自edict）=====
class TaskState(str, enum.Enum):
    """任务状态枚举 — 映射三省六部流程。"""
    Taizi = "Taizi"           # 太子分拣
    Zhongshu = "Zhongshu"     # 中书省起草
    Menxia = "Menxia"         # 门下省审议
    Assigned = "Assigned"     # 尚书省已将任务派发
    Next = "Next"             # 待执行
    Doing = "Doing"           # 六部执行中
    Review = "Review"         # 审查汇总
    Done = "Done"             # 完成
    Blocked = "Blocked"       # 阻塞
    Cancelled = "Cancelled"   # 取消
    Pending = "Pending"       # 待处理


# 终态集合
TERMINAL_STATES = {TaskState.Done, TaskState.Cancelled}

# 状态流转合法路径（完全复制自edict）
STATE_TRANSITIONS = {
    TaskState.Taizi: {TaskState.Zhongshu, TaskState.Cancelled},
    TaskState.Zhongshu: {TaskState.Menxia, TaskState.Cancelled, TaskState.Blocked},
    TaskState.Menxia: {TaskState.Assigned, TaskState.Zhongshu, TaskState.Cancelled},  # 封驳退回中书
    TaskState.Assigned: {TaskState.Doing, TaskState.Next, TaskState.Cancelled, TaskState.Blocked},
    TaskState.Next: {TaskState.Doing, TaskState.Cancelled},
    TaskState.Doing: {TaskState.Review, TaskState.Done, TaskState.Blocked, TaskState.Cancelled},
    TaskState.Review: {TaskState.Done, TaskState.Doing, TaskState.Cancelled},  # 审查不通过退回
    TaskState.Blocked: {TaskState.Taizi, TaskState.Zhongshu, TaskState.Menxia, TaskState.Assigned, TaskState.Doing},
}

# 状态 → Agent 映射
STATE_AGENT_MAP = {
    TaskState.Taizi: "taizi",
    TaskState.Zhongshu: "zhongshu",
    TaskState.Menxia: "menxia",
    TaskState.Assigned: "shangshu",
    TaskState.Review: "shangshu",
}

# 组织 → Agent 映射（六部）
ORG_AGENT_MAP = {
    "户部": "hubu",
    "礼部": "libu",
    "兵部": "bingbu",
    "刑部": "xingbu",
    "工部": "gongbu",
    "吏部": "libu_hr",
}

# 六部列表
SIX_DEPARTMENTS = ["户部", "礼部", "兵部", "刑部", "工部", "吏部"]


# ===== 事件主题常量（完全复制自edict）=====
TOPIC_TASK_CREATED = "task.created"
TOPIC_TASK_PLANNING_REQUEST = "task.planning.request"
TOPIC_TASK_PLANNING_COMPLETE = "task.planning.complete"
TOPIC_TASK_REVIEW_REQUEST = "task.review.request"
TOPIC_TASK_REVIEW_RESULT = "task.review.result"
TOPIC_TASK_DISPATCH = "task.dispatch"
TOPIC_TASK_STATUS = "task.status"
TOPIC_TASK_COMPLETED = "task.completed"
TOPIC_TASK_CLOSED = "task.closed"
TOPIC_TASK_REPLAN = "task.replan"
TOPIC_TASK_STALLED = "task.stalled"
TOPIC_TASK_ESCALATED = "task.escalated"


# ===== 事件总线（内存版）=====
class EventBus:
    """内存事件总线 - 简化版，不需要Redis"""
    
    def __init__(self):
        self._subscribers = []
    
    def subscribe(self, topic: str, callback):
        self._subscribers.append((topic, callback))
    
    async def publish(self, topic: str, trace_id: str, event_type: str, producer: str, payload: dict):
        """发布事件到所有订阅者"""
        event = {
            "topic": topic,
            "trace_id": trace_id,
            "event_type": event_type,
            "producer": producer,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        for t, cb in self._subscribers:
            if t == topic or t == "*":
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(event)
                    else:
                        cb(event)
                except Exception as e:
                    log.error(f"Event callback error: {e}")


# ===== 任务模型（完全版）=====
class Task:
    """三省六部任务 - 完整版"""
    
    def __init__(
        self,
        task_id: str,
        title: str,
        description: str = "",
        priority: str = "中",
        state: TaskState = TaskState.Taizi,
        org: str = "太子",
        official: str = "",
        now: str = "",
        eta: str = "-",
        block: str = "无",
        trace_id: str = None,
        flow_log: list = None,
        progress_log: list = None,
        todos: list = None,
        scheduler: dict = None,
        tags: list = None,
        meta: dict = None,
        creator: str = "emperor",
        assignee_org: str = None,
        created_at: str = None,
        updated_at: str = None,
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.priority = priority
        self.state = state
        self.org = org
        self.official = official
        self.now = now
        self.eta = eta
        self.block = block
        self.trace_id = trace_id or str(uuid.uuid4())
        self.flow_log = flow_log or []
        self.progress_log = progress_log or []
        self.todos = todos or []
        self.scheduler = scheduler or {}
        self.tags = tags or []
        self.meta = meta or {}
        self.creator = creator
        self.assignee_org = assignee_org
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.updated_at = updated_at or datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "state": self.state.value,
            "org": self.org,
            "official": self.official,
            "now": self.now,
            "eta": self.eta,
            "block": self.block,
            "trace_id": self.trace_id,
            "flow_log": self.flow_log,
            "progress_log": self.progress_log,
            "todos": self.todos,
            "scheduler": self.scheduler,
            "tags": self.tags,
            "meta": self.meta,
            "creator": self.creator,
            "assignee_org": self.assignee_org,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        d = d.copy()
        d["state"] = TaskState(d.get("state", "Taizi"))
        d["scheduler"] = d.get("scheduler") or {}
        return cls(**d)


# ===== 任务服务（完全版）=====
class TaskService:
    """三省六部任务服务 - 完整版（参考edict-main）"""
    
    def __init__(self, event_bus: EventBus, tasks_file: pathlib.Path = None):
        self.bus = event_bus
        self._tasks_file = tasks_file or pathlib.Path(__file__).parent / "tasks.json"
        self._tasks = {}
        self._load_tasks()
    
    def _load_tasks(self):
        """从文件加载任务"""
        if self._tasks_file.exists():
            try:
                data = json.loads(self._tasks_file.read_text(encoding='utf-8'))
                self._tasks = {k: Task.from_dict(v) for k, v in data.items()}
            except Exception as e:
                log.error(f"Load tasks error: {e}")
                self._tasks = {}
    
    def _save_tasks(self):
        """保存任务到文件"""
        data = {k: v.to_dict() for k, v in self._tasks.items()}
        self._tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    
    # ── 创建 ──
    
    async def create_task(
        self,
        title: str,
        description: str = "",
        priority: str = "中",
        assignee_org: str | None = None,
        creator: str = "emperor",
        tags: list[str] | None = None,
        initial_state: TaskState = TaskState.Taizi,
        meta: dict | None = None,
    ) -> Task:
        """创建任务并发布 task.created 事件"""
        now = datetime.now(timezone.utc)
        trace_id = str(uuid.uuid4())
        
        # 生成task_id
        task_id = f"JJC-{now.strftime('%Y%m%d')}-{len(self._tasks) + 1:03d}"
        
        task = Task(
            task_id=task_id,
            title=title,
            description=description,
            priority=priority,
            state=initial_state,
            trace_id=trace_id,
            creator=creator,
            assignee_org=assignee_org,
            tags=tags or [],
            flow_log=[{
                "from": None,
                "to": initial_state.value,
                "agent": "system",
                "reason": "任务创建",
                "ts": now.isoformat(),
            }],
            meta=meta or {},
        )
        
        self._tasks[task_id] = task
        self._save_tasks()
        
        # 发布事件
        await self.bus.publish(
            topic=TOPIC_TASK_CREATED,
            trace_id=trace_id,
            event_type="task.created",
            producer="task_service",
            payload={
                "task_id": task_id,
                "title": title,
                "state": initial_state.value,
                "priority": priority,
                "assignee_org": assignee_org,
            },
        )
        
        log.info(f"Created task {task_id}: {title} [{initial_state.value}]")
        return task
    
    # ── 状态流转 ──
    
    async def transition_state(
        self,
        task_id: str,
        new_state: TaskState,
        agent: str = "system",
        reason: str = "",
    ) -> Task:
        """执行状态流转，校验合法性"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        old_state = task.state
        
        # 校验合法流转
        allowed = STATE_TRANSITIONS.get(old_state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {old_state.value} → {new_state.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        
        task.state = new_state
        task.updated_at = datetime.now(timezone.utc).isoformat()
        
        # 记入 flow_log
        flow_entry = {
            "from": old_state.value,
            "to": new_state.value,
            "agent": agent,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        task.flow_log = [*task.flow_log, flow_entry]
        
        self._save_tasks()
        
        # 发布事件
        topic = TOPIC_TASK_COMPLETED if new_state in TERMINAL_STATES else TOPIC_TASK_STATUS
        await self.bus.publish(
            topic=topic,
            trace_id=task.trace_id,
            event_type=f"task.state.{new_state.value}",
            producer=agent,
            payload={
                "task_id": task_id,
                "from": old_state.value,
                "to": new_state.value,
                "reason": reason,
            },
        )
        
        log.info(f"Task {task_id} state: {old_state.value} → {new_state.value} by {agent}")
        return task
    
    # ── 派发请求 ──
    
    async def request_dispatch(
        self,
        task_id: str,
        target_agent: str,
        message: str = "",
    ) -> Task:
        """发布 task.dispatch 事件"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        await self.bus.publish(
            topic=TOPIC_TASK_DISPATCH,
            trace_id=task.trace_id,
            event_type="task.dispatch.request",
            producer="task_service",
            payload={
                "task_id": task_id,
                "agent": target_agent,
                "message": message,
                "state": task.state.value,
            },
        )
        
        log.info(f"Dispatch requested: task {task_id} → agent {target_agent}")
        return task
    
    # ── 派发到部门 ──
    
    async def dispatch_to_org(
        self,
        task_id: str,
        org: str,
        agent: str = "system",
    ) -> Task:
        """派发任务到具体部门（六部）"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        if org not in SIX_DEPARTMENTS:
            raise ValueError(f"Invalid org: {org}. Must be one of {SIX_DEPARTMENTS}")
        
        task.org = org
        task.assignee_org = org
        task.updated_at = datetime.now(timezone.utc).isoformat()
        
        # 记入 flow_log
        flow_entry = {
            "from": task.state.value,
            "to": "Assigned",
            "agent": agent,
            "reason": f"派发到{org}",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        task.flow_log = [*task.flow_log, flow_entry]
        
        # 更新状态为 Assigned
        old_state = task.state
        task.state = TaskState.Assigned
        
        self._save_tasks()
        
        # 发布派发事件
        await self.bus.publish(
            topic=TOPIC_TASK_DISPATCH,
            trace_id=task.trace_id,
            event_type="task.dispatch",
            producer=agent,
            payload={
                "task_id": task_id,
                "org": org,
                "from": old_state.value,
                "to": "Assigned",
            },
        )
        
        log.info(f"Task {task_id} dispatched to {org}")
        return task
    
    # ── 进度/备注更新 ──
    
    async def add_progress(
        self,
        task_id: str,
        agent: str,
        content: str,
    ) -> Task:
        """添加进度记录"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        entry = {
            "agent": agent,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        task.progress_log = [*task.progress_log, entry]
        task.updated_at = datetime.now(timezone.utc).isoformat()
        
        self._save_tasks()
        return task
    
    async def update_todos(
        self,
        task_id: str,
        todos: list[dict],
    ) -> Task:
        """更新待办事项"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        task.todos = todos
        task.updated_at = datetime.now(timezone.utc).isoformat()
        
        self._save_tasks()
        return task
    
    async def update_scheduler(
        self,
        task_id: str,
        scheduler: dict,
    ) -> Task:
        """更新调度信息"""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        task.scheduler = scheduler
        task.updated_at = datetime.now(timezone.utc).isoformat()
        
        self._save_tasks()
        return task
    
    # ── 查询 ──
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取单个任务"""
        return self._tasks.get(task_id)
    
    def list_tasks(
        self,
        state: TaskState | None = None,
        assignee_org: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        """任务列表查询"""
        tasks = list(self._tasks.values())
        
        # 过滤
        if state is not None:
            tasks = [t for t in tasks if t.state == state]
        if assignee_org is not None:
            tasks = [t for t in tasks if t.assignee_org == assignee_org]
        if priority is not None:
            tasks = [t for t in tasks if t.priority == priority]
        
        # 排序和分页
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[offset:offset + limit]
    
    def get_live_status(self) -> dict[str, Any]:
        """生成兼容旧 live_status.json 格式的全局状态"""
        active_tasks = {}
        completed_tasks = {}
        
        for t in self._tasks.values():
            d = t.to_dict()
            if t.state in TERMINAL_STATES:
                completed_tasks[str(t.task_id)] = d
            else:
                active_tasks[str(t.task_id)] = d
        
        return {
            "tasks": active_tasks,
            "completed_tasks": completed_tasks,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    
    def count_tasks(self, state: TaskState | None = None) -> int:
        """统计任务数量"""
        if state is None:
            return len(self._tasks)
        return sum(1 for t in self._tasks.values() if t.state == state)
    
    # ── 删除 ──
    
    async def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save_tasks()
            
            # 发布删除事件
            await self.bus.publish(
                topic="task.deleted",
                trace_id="",
                event_type="task.deleted",
                producer="task_service",
                payload={"task_id": task_id},
            )
            return True
        return False


# 为了支持异步回调
import asyncio