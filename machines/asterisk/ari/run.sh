#!/bin/bash
set -e
cd /opt
if [[ -n "$DO_INIT" || -n "$DO_UPDATE" ]]; then
    [ -d asterisk_ari ] && rm -Rf /opt/asterisk_ari
    git clone --depth 1 --branch deploy git.mt-software.de:/git/openerp/modules/asterisk_ari
    echo 'done updating ari'
    exit 0
fi

echo "Waiting for odoo to arrive at port 8069"
while true; do
    if $(nc -z odoo 8069); then
        break
    fi
    sleep 1
done
echo "Odoo arrived! connecting..."

cd /opt/asterisk_ari/connector
python ariconnector.py \
    --username-asterisk $USERNAME_ASTERISK \
    --password-asterisk $PASSWORD_ASTERISK \
    --host-asterisk $HOST_ASTERISK \
    --port-asterisk $PORT_ASTERISK \
    --odoo-host $ODOO_HOST \
    --odoo-port $ODOO_PORT \
    --odoo-user $ODOO_USER \
    --odoo-password $ODOO_PASSWORD \
    --odoo-db $ODOO_DB
