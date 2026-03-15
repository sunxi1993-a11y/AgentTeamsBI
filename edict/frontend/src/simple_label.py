with open('App.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

old = "agent.statusLabel ? agent.statusLabel.replace(/^[🟢🔵🟡⚪]+/, '').trim() : config.text"
new = "agent.statusLabel || config.text"

content = content.replace(old, new)

with open('App.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print('Simplified statusLabel')