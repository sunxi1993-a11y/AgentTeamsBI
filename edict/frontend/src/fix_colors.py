with open('App.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

old = "running: { color: '#10b981', text: '运行中', class: 'status-running' }"
new = "running: { color: '#fbbf24', text: '忙碌', class: 'status-busy' }"

content = content.replace(old, new)

with open('App.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed running status color to yellow')