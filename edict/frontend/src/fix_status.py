with open('App.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

old = "status: a.status === 'running' ? 'online' : a.status === 'unconfigured' ? 'offline' : a.status,"
new = "status: a.status,"

content = content.replace(old, new)

with open('App.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed status mapping')