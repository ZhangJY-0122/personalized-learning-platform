"""Create a local signing secret once without replacing existing configuration."""
import os,secrets
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'.env'
if p.exists():
    if not any(line.startswith('JWT_SECRET=') and len(line.partition('=')[2].strip())>=32 for line in p.read_text().splitlines()):
        raise SystemExit('Existing .env has no valid JWT_SECRET; add at least 32 random characters. File unchanged.')
    print('Existing .env retained')
else:
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write('# Local generated secret; do not commit\nJWT_SECRET='+secrets.token_urlsafe(48)+'\n')
    print('Created private local .env; secret not printed')
