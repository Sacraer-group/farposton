#!/usr/bin/env python3
"""Forced-command SSH entry: load an image and deploy only its validated SHA tag."""
import os,re,subprocess,sys
match=re.fullmatch(r'deploy ([0-9a-f]{40})',os.environ.get('SSH_ORIGINAL_COMMAND',''))
if not match:raise SystemExit('Only deploy <40-hex-sha> is allowed')
subprocess.run(['sudo','-n','docker','load'],stdin=sys.stdin.buffer,check=True)
subprocess.run(['/home/tech/bereg/bin/release.sh',match[1]],check=True)
