#!/usr/bin/env python3
import sys
import os
import shutil
from pathlib import Path

passwd = Path("/etc/passwd")
content = passwd.read_text()
content = content.replace("1000:1000", "{uid}:{uid}".format(uid=os.environ['OWNER_UID']))
passwd.write_text(content)

os.system("chown '{owner}:{owner}' /opt/files".format(owner=os.environ['OWNER_UID']))
os.system("chown '{owner}:{owner}' /home/odoo".format(owner=os.environ['OWNER_UID']))

os.execvp(sys.argv[1], sys.argv[1:])