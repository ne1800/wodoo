import yaml

def after_compose(config, yml, globals):
    dirs = globals['dirs']
    odoodc = yaml.safe_load((dirs['odoo_home'] / 'images/odoo/docker-compose.yml').read_text())

    # transfer settings from odoo_base into odoo, odoo_cronjobs
    for odoomachine in odoodc['services']:
        if odoomachine == 'odoo_base':
            continue
        if odoomachine not in yml['services']:
            continue
        machine = yml['services'][odoomachine]
        for k in ['volumes']:
            machine[k] = []
            for x in yml['services']['odoo_base'][k]:
                machine[k].append(x)
        for k in ['environment']:
            machine.setdefault(k, {})
            if 'odoo_base' in yml['services']:
                for x, v in yml['services']['odoo_base'][k].items():
                    machine[k][x] = v
    if 'odoo_base' in yml['services']:
        yml['services'].pop('odoo_base')

    if config['RESTART_CONTAINERS'] != "1":
        for service in yml['services']:
            # TODO CLEANUP -> more generic instructions ...
            if 'restart' in yml['services'][service] or \
                    (service == 'odoo_cronjobs' and not config['RUN_ODOO_CRONJOBS']) or \
                    (service == 'proxy' and not config['RUN_PROXY']):
                yml['services'][service].pop('restart')
        for service in yml['services']:
            for service_name, run in [
                ('odoo_cronjobs', config['RUN_ODOO_CRONJOBS']),
                ('odoo_queuejobs', config['RUN_ODOO_QUEUEJOBS']),
            ]:
                if service == service_name:
                    if not run:
                        yml['services'][service].pop('restart')