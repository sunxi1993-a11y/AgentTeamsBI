/**
 * AgentMonitorPanel - 三栏布局的Agent监控面板
 * 移植自 agent-monitor/frontend/monitor.html
 */
import { useEffect, useState } from 'react';
import { api, type AgentInfo } from '../api';

// 本地模拟数据（用于演示）
const mockTimeline = [
  { time: '11:35:22', title: '任务分配', desc: '<总指挥> 将任务派发给 <笔杆子>' },
  { time: '11:32:18', title: '任务完成', desc: '<进化官> 完成代码优化' },
  { time: '11:28:45', title: '新任务到达', desc: '<总指挥> 收到主人新任务' },
  { time: '11:25:10', title: '任务分配', desc: '<总指挥> 将任务派发给 <参谋>' },
  { time: '11:20:05', title: 'Agent唤醒', desc: '<交易官> 已上线' },
];

const mockEvents = [
  { time: '11:35:28', title: '任务状态变更', desc: 'JJC-003 进入执行中状态', type: 'info' },
  { time: '11:33:15', title: '数据同步', desc: '与Gateway同步成功', type: 'success' },
  { time: '11:30:00', title: '任务完成', desc: 'JJC-002 已完成审核', type: 'success' },
  { time: '11:28:45', title: '新任务', desc: '收到JJC-004号任务', type: 'info' },
  { time: '11:25:30', title: 'Agent状态变更', desc: '<运营官> 状态: 在线', type: 'info' },
];

const mockRankings = [
  { name: '总指挥', lp: 1520, tier: '王者' },
  { name: '进化官', lp: 1480, tier: '王者' },
  { name: '笔杆子', lp: 1350, tier: '钻石' },
  { name: '参谋', lp: 1280, tier: '钻石' },
  { name: '交易官', lp: 1200, tier: '铂金' },
  { name: '运营官', lp: 1180, tier: '铂金' },
];

const specialtyMap: Record<string, string> = {
  '总指挥': '统筹全局，指挥调度',
  '笔杆子': '内容创作，文案撰写',
  '参谋': '策略分析，决策支持',
  '运营官': '运营管理，流程优化',
  '进化官': '代码优化，技术演进',
  '交易官': '交易执行，资产管理',
  '社区官': '社区运营，用户互动',
};

const statusColors: Record<string, string> = {
  online: '#10b981',
  busy: '#fbbf24',
  idle: '#06b6d4',
  offline: '#6b7280',
};

const typeLabels: Record<string, string> = {
  info: '信息',
  warning: '警告',
  error: '错误',
  success: '成功',
};

interface AgentWithStatus extends AgentInfo {
  status: 'online' | 'busy' | 'idle' | 'offline';
  tasks: number;
}

export default function AgentMonitorPanel() {
  const [agents, setAgents] = useState<AgentWithStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<AgentWithStatus | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  // 获取Agent列表
  useEffect(() => {
    async function loadAgents() {
      try {
        const data = await api.agents();
        // 添加模拟状态数据
        const agentsWithStatus: AgentWithStatus[] = (data.agents || []).map((a: AgentInfo, idx: number) => ({
          ...a,
          status: ['online', 'busy', 'idle', 'offline'][idx % 4] as AgentWithStatus['status'],
          tasks: Math.floor(Math.random() * 20) + 1,
        }));
        setAgents(agentsWithStatus);
      } catch (e) {
        console.error('Failed to load agents:', e);
      } finally {
        setLoading(false);
      }
    }
    loadAgents();
  }, []);

  // 开启详情面板
  const openPanel = (agent: AgentWithStatus) => {
    setSelectedAgent(agent);
    setPanelOpen(true);
  };

  // 关闭详情面板
  const closePanel = () => {
    setPanelOpen(false);
    setSelectedAgent(null);
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '100px 0', color: 'var(--muted)' }}>
        <div className="loading-spinner" style={{
          width: 50, height: 50, border: '3px solid rgba(255,255,255,0.1)',
          borderTopColor: '#667eea', borderRadius: '50%',
          animation: 'spin 1s linear infinite', margin: '0 auto 20px'
        }} />
        <p>正在加载数据...</p>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', gap: 16, padding: '0 20px 20px' }}>
      {/* 头部统计栏 */}
      <div style={{
        display: 'flex', justifyContent: 'center', gap: 16, flexShrink: 0,
        background: 'rgba(128, 128, 128, 0.3)', borderRadius: 50, padding: '8px 20px',
        border: '1px solid rgba(255, 255, 255, 0.1)', backdropFilter: 'blur(10px)'
      }}>
        <StatPill label="团队人数" value={agents.length} />
        <StatPill label="在线" value={agents.filter(a => a.status === 'online').length} />
        <StatPill label="忙碌" value={agents.filter(a => a.status === 'busy').length} />
        <StatPill label="总任务" value={agents.reduce((sum, a) => sum + a.tasks, 0)} />
      </div>

      {/* 三栏布局 */}
      <div className="dashboard" style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(280px, 0.8fr) 2.4fr minmax(280px, 0.8fr)',
        gap: 20, flex: 1, minHeight: 0, overflow: 'hidden'
      }}>
        {/* 左栏：监督日志 */}
        <div className="card" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div className="card-header">
            <div>
              <div className="card-title">监督日志</div>
              <div className="card-subtitle">记录团队任务分配与完成情况</div>
            </div>
            <div className="update-time">刚刚</div>
          </div>
          <div className="timeline-content" style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12, padding: 10 }}>
            {mockTimeline.map((item, idx) => (
              <div key={idx} className="timeline-item" style={{
                padding: '12px 16px', background: 'rgba(255, 255, 255, 0.05)',
                borderRadius: 10, borderLeft: '3px solid var(--accent)', transition: 'all 0.3s',
                border: '1px solid rgba(255, 255, 255, 0.08)'
              }}>
                <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>{item.time}</div>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>{item.title}</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}
                  dangerouslySetInnerHTML={{ __html: item.desc.replace(/<([^>]+)>/g, '<span style="color:#f59e0b;font-weight:500;font-size:13px;"><$1></span>') }}
                />
              </div>
            ))}
          </div>
        </div>

        {/* 中栏：工作状态 + 排行榜 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 15, minHeight: 0 }}>
          {/* Agent卡片 */}
          <div className="card" style={{ flex: '1.2', overflow: 'hidden' }}>
            <div className="card-header">
              <div>
                <div className="card-title">工作状态</div>
                <div className="card-subtitle">团队Agent当前在线状态与任务负载</div>
              </div>
              <div className="update-time">刚刚</div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, padding: 10, overflow: 'auto', maxHeight: 400 }}>
              {agents.map(agent => (
                <AgentCard key={agent.id} agent={agent} onClick={() => openPanel(agent)} />
              ))}
              {/* 补充空位到6个（3x2=6）或9个（3x3=9） */}
              {agents.length < 6 && Array.from({ length: 6 - agents.length }).map((_, i) => (
                <div key={`empty-${i}`} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  height: 90, background: 'rgba(255,255,255,0.02)', borderRadius: 12,
                  border: '1px dashed rgba(255,255,255,0.1)'
                }}>
                  <span style={{ color: 'var(--muted)', fontSize: 12 }}>待配置</span>
                </div>
              ))}
            </div>
          </div>
          {/* 排行榜 */}
          <div className="card" style={{ flex: '0.8', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div className="card-header">
              <div>
                <div className="card-title">段位排名</div>
                <div className="card-subtitle">根据任务完成情况的实时排行</div>
              </div>
              <div className="update-time">刚刚</div>
            </div>
            <div className="ranking-container" style={{ flex: 1, overflowY: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {mockRankings.map((r, idx) => (
                <div key={idx} style={{ display: 'flex', alignItems: 'center', height: 32 }}>
                  <div style={{ flexGrow: 1, height: 32, backgroundColor: 'rgba(255, 255, 255, 0.1)', borderRadius: 16, position: 'relative', overflow: 'visible' }}>
                    <div style={{
                      height: '100%', background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
                      borderRadius: 16, position: 'absolute', left: 0, top: 0,
                      width: `${Math.max(10, ((r.lp - 1100) / 500 * 100))}%`
                    }} />
                    <span style={{
                      position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                      fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap'
                    }}>{r.name} - {r.tier}</span>
                  </div>
                  <div style={{ width: 60, textAlign: 'right', fontSize: 16, fontWeight: 800, color: 'var(--accent)', marginLeft: 8 }}>
                    {r.lp}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 右栏：事件流 */}
        <div className="card" style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div className="card-header">
            <div>
              <div className="card-title">实时事件流</div>
              <div className="card-subtitle">团队实时动态与系统状态变更</div>
            </div>
            <div className="update-time">刚刚</div>
          </div>
          <div className="events-stream" style={{ flex: 1, overflowY: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {mockEvents.map((event, idx) => (
              <div key={idx} className={`event-card ${event.type}`} style={{
                padding: '12px 16px 12px 32px', background: 'rgba(255, 255, 255, 0.05)',
                borderRadius: 12, position: 'relative', border: '1px solid rgba(255, 255, 255, 0.08)',
                marginBottom: 4
              }}>
                <span className={`event-badge badge-${event.type}`} style={{
                  position: 'absolute', top: 8, right: 8, fontSize: 10, padding: '3px 10px',
                  borderRadius: 20, fontWeight: 600, letterSpacing: '0.5px',
                  background: event.type === 'success' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(59, 130, 246, 0.2)',
                  color: event.type === 'success' ? '#34d399' : '#60a5fa',
                  border: `1px solid ${event.type === 'success' ? 'rgba(16, 185, 129, 0.4)' : 'rgba(59, 130, 246, 0.4)'}`
                }}>{typeLabels[event.type]}</span>
                <div style={{ fontSize: 13, color: 'var(--muted)', fontWeight: 500 }}>{event.time}</div>
                <div style={{ fontWeight: 600, marginBottom: 4, marginTop: 4, fontSize: 14, color: 'var(--text-primary)' }}>{event.title}</div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>{event.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 滑出面板遮罩 */}
      <div className={`panel-overlay ${panelOpen ? 'active' : ''}`} onClick={closePanel} style={{
        position: 'fixed', top: 0, left: 0, width: '100%', height: '100%',
        background: 'rgba(0, 0, 0, 0.5)', opacity: panelOpen ? 1 : 0,
        visibility: panelOpen ? 'visible' : 'hidden', transition: 'all 0.3s ease', zIndex: 999,
        pointerEvents: panelOpen ? 'auto' : 'none'
      }} />

      {/* 滑出详情面板 */}
      <div style={{
        position: 'fixed', top: '50%', left: 0, transform: panelOpen ? 'translateX(0) translateY(-50%)' : 'translateX(-100%) translateY(-50%)',
        width: 600, height: 800, background: 'rgba(30, 41, 59, 0.95)', backdropFilter: 'blur(30px)',
        borderRight: '1px solid rgba(255, 255, 255, 0.15)', borderRadius: '0 20px 20px 0',
        boxShadow: '10px 0 40px rgba(0, 0, 0, 0.4)', zIndex: 1000, display: 'flex', flexDirection: 'column',
        transition: 'transform 0.3s ease'
      }}>
        {selectedAgent && (
          <>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: 20, borderBottom: '1px solid rgba(255,255,255,0.1)',
              background: 'rgba(55,60,75,0.65)', borderRadius: '0 20px 0 0'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 18, flex: 1 }}>
                <div style={{ width: 72, height: 72 }}>
                  {/* Agent icon */}
                  <svg viewBox="0 0 200 200">
                    <defs>
                      <radialGradient id="vinylGrad" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor="#2a2a2a" />
                        <stop offset="40%" stopColor="#1a1a1a" />
                        <stop offset="100%" stopColor="#000" />
                      </radialGradient>
                      <radialGradient id="labelGrad" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stopColor={statusColors[selectedAgent.status]} />
                        <stop offset="100%" stopColor={statusColors[selectedAgent.status]} stopOpacity={0.8} />
                      </radialGradient>
                    </defs>
                    <circle cx="100" cy="100" r="90" fill="url(#vinylGrad)" />
                    <circle cx="100" cy="100" r="35" fill="url(#labelGrad)" />
                    <circle cx="100" cy="100" r="35" fill="none" stroke="rgba(0,0,0,0.3)" strokeWidth="2" />
                    <circle cx="100" cy="100" r="6" fill="#fff" />
                  </svg>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <h3 style={{ fontSize: 24, fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>{selectedAgent.name}</h3>
                  <span style={{ fontSize: 16, color: 'var(--muted)' }}>{specialtyMap[selectedAgent.name] || selectedAgent.role}</span>
                  <span style={{
                    fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10,
                    border: `1px solid ${statusColors[selectedAgent.status]}`,
                    color: statusColors[selectedAgent.status], background: 'transparent', width: 'fit-content'
                  }}>
                    {selectedAgent.status === 'online' ? '在线' : selectedAgent.status === 'busy' ? '忙碌' : selectedAgent.status === 'idle' ? '空闲' : '离线'}
                  </span>
                </div>
              </div>
              <button onClick={closePanel} style={{
                width: 32, height: 32, borderRadius: '50%', background: 'rgba(255,255,255,0.1)',
                border: 'none', color: 'var(--text-secondary)', fontSize: 18, cursor: 'pointer'
              }}>×</button>
            </div>
            <div style={{ flex: 1, padding: 20, display: 'flex', flexDirection: 'column', gap: 15, overflow: 'hidden' }}>
              <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)' }}>Agent 详情</div>
              <div style={{ width: '100%', height: 1, background: 'rgba(255,255,255,0.2)' }} />
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <InfoRow label="Agent ID" value={selectedAgent.id} />
                <InfoRow label="角色" value={selectedAgent.role} />
                <InfoRow label="任务数" value={`${selectedAgent.tasks} 个`} />
                <InfoRow label="状态" value={selectedAgent.status} />
                <InfoRow label="擅长" value={specialtyMap[selectedAgent.name] || '通用技能'} />
              </div>

              <div style={{ marginTop: 20, flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: 12, padding: 20, overflow: 'auto' }}>
                <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>最近活动</div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
                  <div>• 11:35 - 完成任务 JJC-003</div>
                  <div>• 11:20 - 开始执行任务 JJC-004</div>
                  <div>• 11:10 - 收到新任务分配</div>
                  <div>• 10:55 - 与总指挥同步状态</div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* 页脚 */}
      <div style={{ textAlign: 'center', padding: '12px 0', color: 'rgba(255,255,255,0.7)', fontSize: 11, flexShrink: 0 }}>
        <p>- 云端的黑猫 - Agent监控 • 数据实时更新 • 版本: v1.0</p>
      </div>
    </div>
  );
}

// 小组件：统计胶囊
function StatPill({ label, value }: { label: string; value: number }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '10px 20px',
      borderRadius: 40, background: 'linear-gradient(180deg, #151922 0%, #0d1017 100%)',
      boxShadow: '0 4px 6px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05)',
      transition: 'all 0.3s ease', minWidth: 100
    }}>
      <span style={{ fontSize: 14, color: 'var(--text-secondary)', fontWeight: 500 }}>{label}: </span>
      <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginLeft: 6 }}>{value}</span>
    </div>
  );
}

// Agent卡片组件
function AgentCard({ agent, onClick }: { agent: AgentWithStatus; onClick: () => void }) {
  const color = statusColors[agent.status] || '#6b7280';
  const statusText = agent.status === 'online' ? '在线' : agent.status === 'busy' ? '忙碌' : agent.status === 'idle' ? '空闲' : '离线';

  return (
    <div onClick={onClick} style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      width: '100%', height: 90, padding: '16px 12px',
      background: 'rgba(55, 60, 75, 0.65)', backdropFilter: 'blur(20px)', borderRadius: 12,
      boxShadow: '0 0 12px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.05)',
      color: '#fff', cursor: 'pointer', transition: 'all 0.3s ease'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
        <div style={{ width: 48, height: 48, flexShrink: 0, filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.3))' }}>
          <svg viewBox="0 0 200 200">
            <defs>
              <radialGradient id={`vg-${agent.id}`} cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="#2a2a2a" />
                <stop offset="40%" stopColor="#1a1a1a" />
                <stop offset="100%" stopColor="#000" />
              </radialGradient>
              <radialGradient id={`lg-${agent.id}`} cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor={color} />
                <stop offset="100%" stopColor={color} stopOpacity={0.8} />
              </radialGradient>
            </defs>
            <circle cx="100" cy="100" r="90" fill={`url(#vg-${agent.id})`} />
            <circle cx="100" cy="100" r="35" fill={`url(#lg-${agent.id})`} />
            <circle cx="100" cy="100" r="35" fill="none" stroke="rgba(0,0,0,0.3)" strokeWidth="2" />
            <circle cx="100" cy="100" r="6" fill="#fff" />
          </svg>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0, lineHeight: 1.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{agent.name}</h3>
            <span style={{
              fontSize: 9, fontWeight: 600, padding: '1px 6px', borderRadius: 10,
              border: `1px solid ${color}`, color, background: 'transparent', whiteSpace: 'nowrap'
            }}>{statusText}</span>
          </div>
          <p style={{ margin: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 12, color: 'var(--muted)' }}>
            擅长：{specialtyMap[agent.name] || agent.role}
          </p>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        <div style={{
          backgroundColor: '#fff', color: '#007aff', fontSize: 11, fontWeight: 600,
          padding: '4px 10px', borderRadius: 20, whiteSpace: 'nowrap'
        }}>{agent.tasks}个</div>
        <p style={{ fontSize: 9, color: 'rgba(255,255,255,0.5)', margin: 0 }}>任务</p>
      </div>
    </div>
  );
}

// 信息行组件
function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center' }}>
      <span style={{ fontSize: 14, color: 'var(--muted)', width: 80 }}>{label}</span>
      <span style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 500 }}>{value}</span>
    </div>
  );
}