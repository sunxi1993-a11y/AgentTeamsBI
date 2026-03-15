import React, { useEffect, useState, useRef } from 'react';
import './index.css';

// ==================== 类型定义 ====================

/** 代理基础信息接口 */
interface AgentInfo { 
  id: string; 
  label?: string; 
  role: string; 
}

/** 带有运行状态的代理完整信息接口 */
interface AgentWithStatus extends AgentInfo {
  name: string; 
  emoji?: string; 
  // 状态枚举：在线、忙碌、空闲、离线
  status: 'online' | 'busy' | 'idle' | 'offline'; 
  statusLabel?: string;
  tasks: number; // 任务数量
  sessions?: number; // 会话数量
  lastActive?: string | null; // 最后活跃时间
  hasWorkspace?: boolean; // 是否有工作区
  processAlive?: boolean; // 进程是否存活
}

/** 时间轴条目接口（用于日志） */
interface TimelineItem { 
  time: string; 
  title: string; 
  desc: string; 
}

/** 事件流条目接口（包含类型区分） */
interface EventItem { 
  time: string; 
  title: string; 
  desc: string; 
  type: 'info' | 'warning' | 'error' | 'success'; 
  sort_key?: string; // 可选的排序键
}

/** 排位赛数据项接口 */
interface RankingItem { 
  name: string; 
  lp: number; // 段位积分
  tier: string; // 段位等级
}

// ==================== 配置常量 ====================

/** 状态样式配置：映射状态到颜色、文本和 CSS 类名 */
const statusConfig: Record<string, { color: string; text: string; class: string }> = {
  online: { color: '#10b981', text: '在线', class: 'status-online' },
  busy: { color: '#fbbf24', text: '忙碌', class: 'status-busy' },
  running: { color: '#fbbf24', text: '忙碌', class: 'status-busy' }, // 兼容旧数据
  idle: { color: '#06b6d4', text: '空闲', class: 'status-idle' },
  offline: { color: '#06b6d4', text: '空闲', class: 'status-idle' }, // 离线也显示为空闲色
  unconfigured: { color: '#ef4444', text: '未配置', class: 'status-unconfigured' },
};

/** 事件类型样式配置 */
const typeConfig: Record<string, { label: string; class: string; color: string }> = {
  info: { label: '信息', class: 'info', color: '#60a5fa' },
  success: { label: '成功', class: 'success', color: '#34d399' },
  warning: { label: '警告', class: 'warning', color: '#fbbf24' },
  error: { label: '错误', class: 'error', color: '#f87171' },
};

// ==================== 子组件：粒子背景 ====================

/** 
 * 粒子背景组件
 * 使用 Canvas 绘制漂浮粒子，支持粒子间连线及鼠标交互牵引效果
 */
const ParticleBackground = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    
    let particles: any[] = [];
    let animationFrameId: number;
    // 鼠标位置状态
    let mouse = { x: null as number | null, y: null as number | null };

    // 初始化/重置画布尺寸并生成粒子
    const resizeCanvas = () => { 
      canvas.width = window.innerWidth; 
      canvas.height = window.innerHeight; 
      initParticles(); 
    };

    // 生成随机粒子
    const initParticles = () => {
      particles = [];
      for (let i = 0; i < 120; i++) {
        particles.push({
          x: Math.random() * canvas.width, 
          y: Math.random() * canvas.height,
          vx: (Math.random() - 0.5) * 0.5, // X 轴速度
          vy: (Math.random() - 0.5) * 0.5, // Y 轴速度
          radius: Math.random() * 2 + 1,   // 半径
        });
      }
    };

    // 核心绘制循环
    const drawParticles = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      
      // 1. 更新并绘制单个粒子
      particles.forEach((p) => {
        p.x += p.vx; 
        p.y += p.vy;
        // 边界反弹
        if (p.x < 0 || p.x > canvas.width) p.vx *= -1;
        if (p.y < 0 || p.y > canvas.height) p.vy *= -1;
        
        ctx.beginPath(); 
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255,255,255,0.7)'; 
        ctx.fill();
      });

      // 2. 绘制粒子之间的连线 (距离 < 150px)
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const p1 = particles[i], p2 = particles[j];
          const dist = Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2);
          if (dist < 150) {
            ctx.beginPath(); 
            // 距离越近线条越不透明
            ctx.strokeStyle = `rgba(255,255,255,${0.2 * (1 - dist / 150)})`;
            ctx.lineWidth = 1; 
            ctx.moveTo(p1.x, p1.y); 
            ctx.lineTo(p2.x, p2.y); 
            ctx.stroke();
          }
        }
      }

      // 3. 绘制鼠标与附近粒子的连线 (距离 < 200px)
      if (mouse.x !== null && mouse.y !== null) {
        const mx = mouse.x;
        const my = mouse.y;
        particles.forEach(p => {
          const dist = Math.sqrt((mx - p.x) ** 2 + (my - p.y) ** 2);
          if (dist < 200) {
            ctx.beginPath(); 
            ctx.strokeStyle = `rgba(255,255,255,${0.4 * (1 - dist / 200)})`;
            ctx.moveTo(mx, my); 
            ctx.lineTo(p.x, p.y); 
            ctx.stroke();
          }
        });
      }

      animationFrameId = requestAnimationFrame(drawParticles);
    };

    // 启动
    resizeCanvas(); 
    drawParticles();

    // 事件监听
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('mousemove', e => { mouse.x = e.clientX; mouse.y = e.clientY; });
    window.addEventListener('mouseleave', () => { mouse.x = null; mouse.y = null; });

    // 清理
    return () => {
      window.removeEventListener('resize', resizeCanvas);
      window.removeEventListener('mousemove', () => {}); // 注意：此处原代码移除的是空函数，保持原逻辑不破坏
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return <canvas ref={canvasRef} style={{ position: 'fixed', top: 0, left: 0, zIndex: 0, pointerEvents: 'none' }} />;
};

// ==================== 子组件：胶片风格头像 ====================

/** 
 * 胶片头像组件
 * 使用 SVG 径向渐变模拟黑胶唱片/胶片质感，中心颜色随状态变化
 */
const VinylAvatar = ({ name, status, size = 48 }: { name: string; status: string; size?: number }) => {
  const color = statusConfig[status]?.color || '#6b7280';
  // 生成唯一的 gradient ID 避免冲突
  const gradientId = `vinyl-${name}-${Math.random().toString(36).substr(2, 9)}`;
  const labelGradId = `label-${name}-${Math.random().toString(36).substr(2, 9)}`;

  return (
    <svg width={size} height={size} viewBox="0 0 200 200" style={{ flexShrink: 0, filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.3))' }}>
      <defs>
        {/* 黑胶纹理渐变 */}
        <radialGradient id={gradientId} cx="50%" cy="50%" r="50%" fx="50%" fy="50%">
          <stop offset="0%" style={{ stopColor: '#2a2a2a', stopOpacity: 1 }} />
          <stop offset="40%" style={{ stopColor: '#1a1a1a', stopOpacity: 1 }} />
          <stop offset="45%" style={{ stopColor: '#333333', stopOpacity: 1 }} />
          <stop offset="50%" style={{ stopColor: '#1a1a1a', stopOpacity: 1 }} />
          <stop offset="80%" style={{ stopColor: '#111111', stopOpacity: 1 }} />
          <stop offset="100%" style={{ stopColor: '#000000', stopOpacity: 1 }} />
        </radialGradient>
        {/* 中心标签颜色渐变 (基于状态) */}
        <radialGradient id={labelGradId} cx="50%" cy="50%" r="50%" fx="50%" fy="50%">
          <stop offset="0%" style={{ stopColor: color, stopOpacity: 1 }} />
          <stop offset="100%" style={{ stopColor: color, stopOpacity: 0.8 }} />
        </radialGradient>
      </defs>
      {/* 外圈唱片 */}
      <circle cx="100" cy="100" r="90" fill={`url(#${gradientId})`} />
      {/* 中心标签 */}
      <circle cx="100" cy="100" r="35" fill={`url(#${labelGradId})`} />
      {/* 标签边框 */}
      <circle cx="100" cy="100" r="35" fill="none" stroke="rgba(0,0,0,0.3)" strokeWidth="2" />
      {/* 中心孔 */}
      <circle cx="100" cy="100" r="6" fill="#ffffff" />
    </svg>
  );
};

// ==================== 主应用组件 ====================

export default function App() {
  // --- 状态管理 ---
  const [agents, setAgents] = useState<AgentWithStatus[]>([]); // 代理列表
  const [rankings, setRankings] = useState<RankingItem[]>([]); // 排位数据
  const [events, setEvents] = useState<EventItem[]>([]);       // 系统事件流
  const [taskEvents, setTaskEvents] = useState<EventItem[]>([]); // 任务专用事件
  const [selectedAgent, setSelectedAgent] = useState<AgentWithStatus | null>(null); // 当前选中的代理
  const [workspaceFiles, setWorkspaceFiles] = useState<Record<string, string>>({}); // 工作区文件内容
  const [selectedFile, setSelectedFile] = useState<string>('IDENTITY'); // 当前查看的文件
  const [panelOpen, setPanelOpen] = useState(false); // 代理详情面板开关
  const [calendarOpen, setCalendarOpen] = useState(false); // 日历面板开关
  
  // 日历打开时自动滚动到底部
  useEffect(() => {
    if (calendarOpen) {
      setTimeout(() => {
        const el = document.querySelector('.calendar-body');
        if (el) el.scrollTop = el.scrollHeight;
      }, 100);
    }
  }, [calendarOpen]);
  
  const [dailyData, setDailyData] = useState<any[]>([]); // 每日用量数据
  const [lastUpdate, setLastUpdate] = useState<string>(''); // 最后更新时间
  const [usageData, setUsageData] = useState<any>(null); // 总用量统计

  // --- 辅助映射 ---
  // 文件名 key 到中文显示的映射
  const fileNameMap: Record<string, string> = {
    'IDENTITY': '主人画像',
    'MEMORY': '记忆文件',
    'AGENTS': '代理定义',
    'SOUL': '灵魂/个性',
    'TOOLS': '工具配置',
    'USER': '用户信息',
    'HEARTBEAT': '心跳任务',
    'BOOTSTRAP': '启动配置',
  };

  // 文件展示顺序 (与后端 1 号位逻辑保持一致)
  const fileOrder = ['IDENTITY', 'AGENTS', 'USER', 'SOUL', 'MEMORY', 'HEARTBEAT', 'TOOLS', 'BOOTSTRAP'];

  // --- 数据获取 Effects ---

  // 1. 获取代理状态 (每 5 秒轮询)
  useEffect(() => {
    const fetchAgentsStatus = async () => {
      try {
        const res = await fetch('/api/agents-status');
        const data = await res.json();
        if (data.ok && data.agents) {
          const mapped = data.agents.map((a: any) => ({
            id: a.id,
            name: a.label,
            emoji: a.emoji,
            role: a.role,
            status: a.status,
            statusLabel: a.statusLabel,
            tasks: a.sessions || 0,
            sessions: a.sessions || 0,
            lastActive: a.lastActive,
            hasWorkspace: a.hasWorkspace,
            processAlive: a.processAlive,
          }));
          setAgents(mapped);
          setLastUpdate(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
        }
      } catch (e) {
        console.error('Failed to fetch agents:', e);
      }
    };
    fetchAgentsStatus();
    const interval = setInterval(fetchAgentsStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  // 2. 获取选中代理的工作区文件
  useEffect(() => {
    if (!selectedAgent) {
      setWorkspaceFiles({});
      return;
    }
    const fetchWorkspaceFiles = async () => {
      try {
        const agentId = selectedAgent.id || 'main';
        const res = await fetch(`/api/workspace-files/${agentId}`);
        const data = await res.json();
        if (data.ok && data.files) {
          setWorkspaceFiles(data.files);
          // 默认选中第一个文件
          const fileKeys = Object.keys(data.files);
          if (fileKeys.length > 0) {
            setSelectedFile(fileKeys[0].toUpperCase());
          }
        }
      } catch (e) {
        console.error('Failed to fetch workspace files:', e);
      }
    };
    fetchWorkspaceFiles();
  }, [selectedAgent]);

  // 3. 获取排位数据 (每 5 秒轮询)
  useEffect(() => {
    const fetchRankings = async () => {
      try {
        const res = await fetch('/api/rankings');
        const data = await res.json();
        if (data.ok && data.rankings) {
          setRankings(data.rankings);
        }
      } catch (e) {
        console.error('Failed to fetch rankings:', e);
      }
    };
    fetchRankings();
    const interval = setInterval(fetchRankings, 5000);
    return () => clearInterval(interval);
  }, []);

  // 4. 获取事件流 (每 1 秒轮询)
  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch('/api/events');
        const data = await res.json();
        if (data.ok && data.events) {
          setEvents(data.events);
        }
        if (data.ok && data.taskEvents) {
          setTaskEvents(data.taskEvents);
        }
      } catch (e) {
        console.error('Failed to fetch events:', e);
      }
    };
    fetchEvents();
    const interval = setInterval(fetchEvents, 1000);
    return () => clearInterval(interval);
  }, []);

  // 5. 获取用量成本数据 (每 30 秒轮询)
  useEffect(() => {
    const fetchUsage = async () => {
      try {
        const res = await fetch('/api/usage-cost');
        const data = await res.json();
        if (data.periods) {
          setUsageData(data);
          if (data.daily) {
            setDailyData(data.daily);
          }
        }
      } catch (e) {
        console.error('Failed to fetch usage:', e);
      }
    };
    fetchUsage();
    const interval = setInterval(fetchUsage, 30000);
    return () => clearInterval(interval);
  }, []);

  // --- 统计数据计算 ---
  const onlineCount = agents.filter(a => a.status === 'online').length;
  const totalTasks = agents.reduce((sum, a) => sum + a.tasks, 0);

  return (
    <>
      <ParticleBackground />
      <div className="wrap">
        {/* 顶部导航栏 */}
        <header className="hdr">
          <h1>AGENT TEAMS BI</h1>
          <div className="hdr-stats">
            {/* 今日 Token 用量 */}
            <div className="stat-pill vertical">
              <span>{usageData ? (usageData.periods.find((p:any) => p.key === 'today')?.tokens / 1000000).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + 'M' : '-'}</span>
              <label>Token 用量 - 今日</label>
            </div>
            {/* 本月 Token 用量 */}
            <div className="stat-pill vertical">
              <span>{usageData ? (usageData.periods.find((p:any) => p.key === 'month')?.tokens / 1000000).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + 'M' : '-'}</span>
              <label>Token 用量 - 本月</label>
            </div>
            {/* 5 小时内 API 请求 */}
            <div className="stat-pill vertical">
              <span>{usageData ? (usageData.periods.find((p:any) => p.key === '5h')?.requestCount ?? 0).toLocaleString('en-US') : '-'}</span>
              <label>API 请求次数 - 5h 内</label>
            </div>
            {/* 本周 API 请求 */}
            <div className="stat-pill vertical">
              <span>{usageData ? (usageData.periods.find((p:any) => p.key === 'week')?.requestCount ?? 0).toLocaleString('en-US') : '-'}</span>
              <label>API 请求次数 - 本周</label>
            </div>
            {/* 本月 API 请求 */}
            <div className="stat-pill vertical">
              <span>{usageData ? (usageData.periods.find((p:any) => p.key === 'month')?.requestCount ?? 0).toLocaleString('en-US') : '-'}</span>
              <label>API 请求次数 - 本月</label>
            </div>
            {/* 打开日历按钮 */}
            <div className="stat-pill vertical">
              <span></span>
              <label className="token-calendar-label" onClick={() => setCalendarOpen(true)}>Token 用量日历</label>
            </div>
          </div>
        </header>

        <main className="main-grid">
          {/* 卡片 1: 监督日志 (任务分配与完成) */}
          <section className="card">
            <div className="card-header">
              <div><div className="card-title">监督日志</div><div className="card-subtitle">记录团队任务分配与完成情况</div></div>
              <div className="card-time">刚刚</div>
            </div>
            <div className="card-body">
              <div className="scroll-content">
                {[
                  // 过滤出任务相关的事件
                  ...events.filter(e =>
                    e.title === '任务分配' ||
                    e.title === '任务完成' ||
                    e.title === '任务 completed'
                  ),
                  ...taskEvents
                ].slice(0, 20).map((item, idx) => {
                  // 格式化描述文本：将 agent 名字包裹在 < > 中以便高亮
                  let desc = item.desc;
                  // 规则 1: "总指挥派发给 xxx：" -> "< 总指挥 > 派发给 < xxx > :"
                  desc = desc.replace(/^([^派发]+) 派发给 ([^：]+)：/, '< $1 > 派发给 < $2 > ：');
                  // 规则 2: "xxx 完成：" -> "< xxx > 完成："
                  desc = desc.replace(/^([^<]+) 完成：?/, '< $1 > 完成：');

                  return (
                  <div key={idx} className="timeline-item">
                    <div className="tl-time">{item.time}</div>
                    <div className="tl-title">{item.title}</div>
                    {/* 将 <name> 替换为高亮 span */}
                    <div className="tl-desc" dangerouslySetInnerHTML={{
                      __html: desc.replace(/<([^>]+)>/g, '<span class="tl-highlight">< $1 ></span>')
                    }} />
                  </div>
                )})}
              </div>
            </div>
          </section>

          {/* 右侧列：工作状态 + 段位排名 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '15px', minHeight: 0 }}>
            
            {/* 卡片 2: 工作状态网格 */}
            <section className="card" style={{ flex: '1.2', minHeight: '450px', maxHeight: '450px' }}>
              <div className="card-header">
                <div><div className="card-title">工作状态</div><div className="card-subtitle">忙碌：执行中  在线：待任务  空闲：无活动</div></div>
                <div className="card-time">实时</div>
              </div>
              <div className="card-body">
                <div className="agent-grid">
                  {agents.map(agent => {
                    const config = statusConfig[agent.status];
                    return (
                      <div key={agent.id} className="agent-card" onClick={() => { setSelectedAgent(agent as any); setPanelOpen(true); }}>
                        <div className="ac-left">
                          <VinylAvatar name={agent.name} status={agent.status} size={48} />
                          <div className="ac-info">
                            <div className="ac-name-row">
                              <span className="ac-name">{agent.name}</span>
                              <span className={`ac-status ${config.class}`}>{agent.statusLabel || config.text}</span>
                            </div>
                            <div className="ac-role">{agent.role}</div>
                          </div>
                        </div>
                        <div className="ac-task-count">
                          <span>{agent.tasks}个</span>
                          <span>会话</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>

            {/* 卡片 3: 段位排名 */}
            <section className="card" style={{ flex: '0.8' }}>
              <div className="card-header">
                <div><div className="card-title">段位排名</div><div className="card-subtitle">根据任务完成情况的实时排行</div></div>
                <div className="card-time">实时</div>
              </div>
              <div className="card-body">
                <div className="scroll-content">
                  {rankings.map((r, idx) => {
                    // 计算进度条宽度：基于 LP (基准 1100, 范围 500)
                    const percent = Math.max(10, ((r.lp - 1100) / 500 * 100));
                    return (
                      <div key={idx} className="ranking-item">
                        <div className="rank-bar-wrap">
                          <div className="rank-bar-fill" style={{ width: `${percent}%` }} />
                          <span className="rank-text">{r.name} - {r.tier}</span>
                        </div>
                        <div className="rank-score">{r.lp}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>
          </div>

          {/* 卡片 4: 实时事件流 */}
          <section className="card">
            <div className="card-header">
              <div><div className="card-title">实时事件流</div><div className="card-subtitle">团队实时动态与系统状态变更</div></div>
              <div className="card-time">实时</div>
            </div>
            <div className="card-body">
              <div className="scroll-content">
                {events
                  // 过滤特定类型的事件
                  .filter(e =>
                    e.title?.includes('消息接收') ||
                    e.title?.includes('Agent') ||
                    e.title?.includes('状态') ||
                    e.title?.includes('连接') ||
                    e.title?.includes('唤醒') ||
                    e.title?.includes('登录')
                  )
                  // 按时间倒序排序
                  .sort((a, b) => (b.sort_key || b.time || '').localeCompare(a.sort_key || a.time || ''))
                  .map((event, idx) => {
                  const cfg = typeConfig[event.type];
                  return (
                    <div key={idx} className={`event-item ${event.type}`}>
                      <span className={`ev-tag ${cfg.class}`}>{cfg.label}</span>
                      <div className="ev-time">{event.time}</div>
                      <div className="ev-title">{event.title}</div>
                      {/* 高亮处理：Agent 名称、JJC-ID、系统组件名 */}
                      <div className="ev-desc" dangerouslySetInnerHTML={{
                        __html: event.desc
                          .replace(/<([^>]+)>/g, '<span class="tl-highlight">< $1 ></span>')
                          .replace(/(JJC-[^\s]+)/g, '<span class="ev-highlight">[ $1 ]</span>')
                          .replace(/(Gateway|OpenClaw)/g, '<span class="ev-highlight">[ $1 ]</span>')
                      }} />
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        </main>

        <footer className="footer">
          <p>- 云端的黑猫 - 总控台 • 数据实时更新 • 最后更新：{lastUpdate || '--:--:--'} • 版本：v2.1</p>
        </footer>
      </div>

      {/* 模态框：代理详情面板 (右侧滑出) */}
      <div className={`modal-overlay ${panelOpen ? 'open' : ''}`} onClick={() => setPanelOpen(false)} />
      <div className={`modal-panel ${panelOpen ? 'open' : ''}`}>
        {selectedAgent && (
          <>
            <div className="mp-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 18, flex: 1 }}>
                <VinylAvatar name={selectedAgent.name} status={selectedAgent.status} size={72} />
                <div>
                  <h3 style={{ fontSize: 24, margin: 0, color: '#fff', fontWeight: 700 }}>{selectedAgent.name}</h3>
                  <p style={{ color: '#94a3b8', margin: '4px 0' }}>{selectedAgent.role}</p>
                </div>
              </div>
              <button className="mp-close" onClick={() => setPanelOpen(false)}>×</button>
            </div>
            <div className="mp-body file-panel-body">
              {/* 左侧：文件列表 */}
              <div className="mp-file-list">
                {Object.keys(workspaceFiles).length > 0 ? (
                  // 按预定顺序过滤并渲染存在的文件
                  fileOrder.filter(k => workspaceFiles[k]).map((fileKey) => (
                    <div
                      key={fileKey}
                      className={`file-card ${selectedFile === fileKey.toUpperCase() ? 'active' : ''}`}
                      onClick={() => setSelectedFile(fileKey.toUpperCase())}
                    >
                      <span className="file-label">{fileNameMap[fileKey.toUpperCase()] || fileKey}</span>
                      <span className="file-name">{fileKey.toLowerCase()}.md</span>
                    </div>
                  ))
                ) : (
                  <div className="file-card disabled">
                    <span className="file-label">加载中...</span>
                    <span className="file-name">正在读取文件</span>
                  </div>
                )}
              </div>
              
              {/* 右侧：文件内容预览 */}
              <div className="mp-file-content">
                <div className="content-header">
                  <span className="content-title">{fileNameMap[selectedFile?.toUpperCase()] || selectedFile}</span>
                  <span className="content-desc">文件详情</span>
                </div>
                <div className="content-divider"></div>
                <div className="content-display">
                  <pre style={{whiteSpace: 'pre-wrap', wordBreak: 'break-word'}}>
                    {workspaceFiles[selectedFile?.toUpperCase()] || '暂无内容'}
                  </pre>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* 模态框：Token 用量日历 (左侧滑出) */}
      <div className={`calendar-overlay ${calendarOpen ? 'open' : ''}`} onClick={() => setCalendarOpen(false)} />
      <div className={`calendar-panel ${calendarOpen ? 'open' : ''}`}>
        <div className="calendar-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 18, flex: 1 }}>
            {/* 日历专用图标 */}
            <svg width={72} height={72} viewBox="0 0 200 200" style={{ flexShrink: 0, filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.3))' }}>
              <defs>
                <radialGradient id="cal-vinyl" cx="50%" cy="50%" r="50%" fx="50%" fy="50%">
                  <stop offset="0%" stopColor="#2a2a2a" />
                  <stop offset="40%" stopColor="#1a1a1a" />
                  <stop offset="45%" stopColor="#333333" />
                  <stop offset="50%" stopColor="#1a1a1a" />
                  <stop offset="80%" stopColor="#111111" />
                  <stop offset="100%" stopColor="#000000" />
                </radialGradient>
                <radialGradient id="cal-label" cx="50%" cy="50%" r="50%" fx="50%" fy="50%">
                  <stop offset="0%" stopColor="#ec4899" />
                  <stop offset="100%" stopColor="#be185d" />
                </radialGradient>
              </defs>
              <circle cx="100" cy="100" r="90" fill="url(#cal-vinyl)" />
              <circle cx="100" cy="100" r="35" fill="url(#cal-label)" />
              <circle cx="100" cy="100" r="35" fill="none" stroke="rgba(0,0,0,0.3)" strokeWidth="2" />
              <circle cx="100" cy="100" r="6" fill="#ffffff" />
            </svg>
            <div>
              <h3 style={{ fontSize: 24, margin: 0, color: '#fff', fontWeight: 700 }}>Token 用量日历</h3>
              <p style={{ color: '#94a3b8', margin: '4px 0' }}>2026 年 1-3 月每日消耗</p>
            </div>
          </div>
          <button className="calendar-close" onClick={() => setCalendarOpen(false)}>×</button>
        </div>
        
        <div className="calendar-body">
          {dailyData.length === 0 ? (
            <div style={{color: '#888', textAlign: 'center', padding: '40px 20px'}}>加载中...</div>
          ) : (
            <div style={{width: '100%', maxWidth: 551, margin: '0 auto'}}>
              {(() => {
                // 热力图颜色阶梯
                const colors = ['transparent', '#e5e7eb', '#F8ACBF', '#F47495'];
                const monthNames = ['1 月', '2 月', '3 月', '4 月', '5 月', '6 月', '7 月', '8 月', '9 月', '10 月', '11 月', '12 月'];
                const now = new Date();
                const year = now.getFullYear();
                const currentMonth = now.getMonth();
                const result = [];

                // 遍历从 1 月 到 当前月份
                for (let month = 0; month <= currentMonth; month++) {
                  // 筛选当月数据
                  const monthData = dailyData.filter(d => {
                    const dMonth = parseInt(d.date.split('-')[1]) - 1;
                    return dMonth === month;
                  });

                  // 计算四分位数阈值用于颜色分级
                  const tokensWithData = monthData.filter(d => d.tokens > 0).map(d => d.tokens);
                  let q1, q2, q3;
                  if (tokensWithData.length > 0) {
                    const max = Math.max(...tokensWithData);
                    const min = Math.min(...tokensWithData);
                    const range = max - min;
                    q1 = min + range * 0.25;
                    q2 = min + range * 0.5;
                    q3 = min + range * 0.75;
                  } else {
                    q1 = q2 = q3 = 0;
                  }

                  // 渲染月份标题
                  result.push(
                    <div key={`month-${month}`} style={{textAlign: 'center', fontSize: 18, fontWeight: 900, marginBottom: 10, color: '#fff'}}>
                      {year}年 {monthNames[month]}
                    </div>
                  );

                  // 计算日历网格参数
                  const firstDay = new Date(year, month, 1).getDay();
                  const daysInMonth = new Date(year, month + 1, 0).getDate();

                  result.push(
                    <table key={`table-${month}`} style={{width: '100%', borderCollapse: 'collapse', textAlign: 'center', fontSize: 12, marginBottom: 30}}>
                      <thead>
                        <tr>
                          {['日','一','二','三','四','五','六'].map(d => (
                            <th key={d} style={{padding: 8, color: '#94a3b8'}}>{d}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const rows = [];
                          let cells = [];

                          // 填充月初空白
                          for (let i = 0; i < firstDay; i++) {
                            cells.push(<td key={`empty-${i}`}></td>);
                          }

                          // 填充日期格子
                          for (let day = 1; day <= daysInMonth; day++) {
                            const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
                            const dayData = monthData.find(d => d.date === dateStr);
                            const t = dayData ? dayData.tokens : 0;
                            let colorIdx = 0;

                            // 根据用量决定颜色等级
                            if (t > 0) {
                              if (t >= q3) colorIdx = 3;
                              else if (t >= q2) colorIdx = 2;
                              else if (t >= q1) colorIdx = 1;
                            }

                            // 根据背景色深浅决定文字颜色
                            const textColor = (colorIdx === 3 || colorIdx === 0) ? '#fff' : '#000';

                            cells.push(
                              <td key={day} style={{
                                padding: 8,
                                backgroundColor: t > 0 ? colors[colorIdx] : 'transparent',
                              }}>
                                <div style={{fontSize: 14, fontWeight: 600, color: textColor}}>{day}</div>
                                {t > 0 ? (
                                  <div style={{fontSize: 10, fontWeight: 600, opacity: 0.8, color: textColor}}>
                                    {(t/1000000).toFixed(1)}M
                                  </div>
                                ) : (
                                  <div style={{fontSize: 10, fontWeight: 600, color: '#fff'}}>{'\u00A0'}</div>
                                )}
                              </td>
                            );

                            // 每 7 天换行
                            if ((firstDay + day) % 7 === 0) {
                              rows.push(<tr key={`row-${day}`}>{cells}</tr>);
                              cells = [];
                            }
                          }

                          // 填充月末空白
                          if (cells.length > 0) {
                            while (cells.length < 7) {
                              cells.push(<td key={`empty-end-${cells.length}`}></td>);
                            }
                            rows.push(<tr key="row-end">{cells}</tr>);
                          }

                          return rows;
                        })()}
                      </tbody>
                    </table>
                  );
                }

                return result;
              })()}
            </div>
          )}
        </div>
      </div>
    </>
  );
}