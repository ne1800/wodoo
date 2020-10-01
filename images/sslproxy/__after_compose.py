import sys
import shutil
from pathlib import Path
from odoo_tools import dirs
import inspect
import os
dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

def after_compose(config, yml, globals):
    if config['RUN_SSLPROXY'] != '1':
        return

    nginx_conf = dirs['run'] / 'ssl' / 'nginx' / 'site-confs' / 'default'
    src = (dir / 'nginx.conf.template').read_text()
    subdomains = config.get('SSLPROXY_SUBDOMAINS', "")
    if subdomains:
        subdomains = subdomains.split(",")
        if len(subdomains) > 1:
            raise Exception("only one subdomain supported")

        domain = subdomains[0] + "." + config['SSLPROXY_DOMAIN']
    else:
        domain = config['SSLPROXY_DOMAIN']

    src = src.replace("__DOMAIN__", domain)

    nginx_conf.parent.mkdir(parents=True, exist_ok=True)
    nginx_conf.write_text(src)
