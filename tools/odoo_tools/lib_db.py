import subprocess
import yaml
import arrow
import json
import pipes
import re
import traceback
import sys
import shutil
import hashlib
import os
import tempfile
import click
import inquirer
from datetime import datetime
from .tools import __replace_all_envs_in_str
from .tools import _dropdb
from .tools import __assert_file_exists
from .tools import __exists_db
from .tools import __set_db_ownership
from .tools import __safe_filename
from .tools import remove_webassets
from .tools import __read_file
from .tools import __write_file
from .tools import _askcontinue
from .tools import __append_line
from .tools import __get_odoo_commit
from .tools import __dcrun, __dc, __remove_postgres_connections, __execute_sql, __dcexec
from .tools import __start_postgres_and_wait
from .tools import get_volume_names
from . import cli, pass_config, dirs, files, Commands
from .lib_clickhelpers import AliasedGroup
from .tools import __hash_odoo_password
from . import PROJECT_NAME


def _get_cmd_butter_volume():
    drunc = ["sudo", "runc", "--root", "/run/docker/plugins/runtime-root/plugins.moby/"]
    container_id = subprocess.check_output(drunc + ["list"]).decode('utf-8').split('\n')[1].split(" ")[0]
    buttervolume = drunc + ['exec', '-t', container_id, 'buttervolume']
    return buttervolume

def __get_postgres_volume_name(config):
    return PROJECT_NAME + "_" + 'ODOO_POSTGRES_VOLUME'

@cli.group(cls=AliasedGroup)
@pass_config
def db(config):
    """
    Database related actions.
    """
    click.echo("database-name: {}, in ram: {}".format(config.dbname, config.run_postgres_in_ram))

@cli.group(cls=AliasedGroup)
@pass_config
def snapshot(config):
    pass

def __assert_btrfs(config):
    if not config.run_btrfs:
        click.echo("Please enable RUN_BTRFS=1 and make sure, that volumes are using the anybox/buttervolume docker plugin")
        sys.exit(-1)

def __get_snapshots(config):
    snapshots = [x for x in subprocess.check_output(_get_cmd_butter_volume() + ["snapshots"]).decode('utf-8').split("\n") if x]
    # filter to current customs
    name = __get_postgres_volume_name(config)
    snapshots = [x for x in snapshots if name in x in x]
    return snapshots

def _try_get_date_from_snap(snap_name):
    try:
        snap_name = snap_name.split("@")[-1][:19]
        d = datetime.strptime(snap_name, '%Y-%m-%dT%H:%M:%S')
        tz = os.getenv("TZ", "")
        if tz:
            d = arrow.get(d).to(tz).datetime
        return d
    except Exception:
        return None

def __choose_snapshot(config, take=False):
    snapshots = __get_snapshots(config)
    mappings = __get_snapshot_db()
    snapshots2 = []
    used_mappings = {}
    for x in snapshots:
        snap_name = mappings.get(x, x)
        if x != snap_name:
            d = _try_get_date_from_snap(x)
            if d:
                d = d.strftime("%Y-%m-%d %H:%M:%S")
            else:
                d = '-'
            snap_name_with_date = "{0:<33} [{1}]".format(snap_name, d)
            used_mappings[snap_name] = x
            used_mappings[snap_name_with_date] = x
            snapshots2.append(snap_name_with_date)

    if take:
        return used_mappings[take]
    snapshots2 = list(reversed(snapshots2))

    snapshot = inquirer.prompt([inquirer.List('snapshot', "", choices=snapshots2)])['snapshot']
    snapshot = used_mappings[snapshot]
    return snapshot

def __get_snapshot_db():
    d = files['run/snapshot_mappings.txt']
    if not d.exists():
        __set_snapshot_db({})
    return yaml.safe_load(d.read_text())

def __set_snapshot_db(values):
    d = files['run/snapshot_mappings.txt']
    d.write_text(yaml.dump(values, default_flow_style=False))

@snapshot.command(name="list")
@pass_config
def do_list(config):
    __assert_btrfs(config)
    snapshots = __get_snapshots(config)
    mappings = __get_snapshot_db()

    for snap in snapshots:
        print(mappings.get(snap, snap))

@snapshot.command(name="save")
@click.argument('name', required=True)
@pass_config
def snapshot_make(config, name):
    __assert_btrfs(config)

    values = __get_snapshot_db()
    volume_name = __get_postgres_volume_name(config)
    __dc(['stop', '-t 1'] + ['postgres'])

    # remove existing snaps
    for snapshot, snapname in list(values.items()):
        if snapname == name:
            subprocess.check_call(_get_cmd_butter_volume() + ["rm", snapshot])
            del values[snapshot]
            __set_snapshot_db(values)
    snapshot = subprocess.check_output(_get_cmd_butter_volume() + ["snapshot", volume_name]).decode('utf-8').strip()
    __dc(['up', '-d'] + ['postgres'])
    if name:
        values = __get_snapshot_db()
        values[snapshot] = name
        __set_snapshot_db(values)

    click.echo("Made snapshot: {}".format(snapshot))

@snapshot.command(name="restore")
@click.option('-c', '--clear', is_flag=True, help="clears all snapshots afterwards")
@click.argument('name', required=False)
@pass_config
@click.pass_context
def snapshot_restore(ctx, config, clear, name):
    __assert_btrfs(config)

    snapshot = __choose_snapshot(config, take=name)
    if not snapshot:
        return
    __dc(['stop', '-t 1'] + ['postgres'])
    subprocess.check_call(_get_cmd_butter_volume() + ["restore", snapshot])
    if clear:
        ctx.invoke(snapshot_clear_all)

    __dc(['up', '-d'] + ['postgres'])

@snapshot.command(name="remove")
@click.argument('name', required=False)
@pass_config
@click.pass_context
def snapshot_remove(ctx, config, name):
    __assert_btrfs(config)

    snapshot = __choose_snapshot(config, take=name)
    if not snapshot:
        return
    __dc(['stop', '-t 1'] + ['postgres'])
    subprocess.check_call(_get_cmd_butter_volume() + ["rm", snapshot])
    __dc(['up', '-d'] + ['postgres'])
    values = __get_snapshot_db()
    if snapshot in values:
        del values[snapshot]
        __set_snapshot_db(values)

@snapshot.command(name="clear", help="Removes all snapshots")
@pass_config
@click.pass_context
def snapshot_clear_all(ctx, config):
    __assert_btrfs(config)

    snapshots = __get_snapshots(config)
    if snapshots:
        __dc(['stop', '-t 1'] + ['postgres'])
        for snap in snapshots:
            subprocess.check_call(_get_cmd_butter_volume() + ["rm", snap])
        __dc(['up', '-d'] + ['postgres'])

    ctx.invoke(do_list)

@db.command()
@click.argument('dbname', required=True)
@pass_config
def drop_db(config, dbname):

    if not (config.devmode or config.force):
        click.echo("Either DEVMODE or force required")
        sys.exit(-1)
    __remove_postgres_connections(dbname)
    __execute_sql("drop database {};".format(dbname), dbname='template1', notransaction=True)
    click.echo("Database {} dropped.".format(dbname))

@db.command()
@pass_config
def pgactivity(config):
    if config.run_postgres:
        __dcexec(["postgres", 'pg_activity'])


@db.command()
@pass_config
def turn_into_dev(config):
    if not config.devmode:
        raise Exception("""When applying this sql scripts, the database is not usable anymore for production environments.
Please set DEVMODE=1 to allow this""")
    __turn_into_devdb(config.get_odoo_conn())

@db.command()
@click.argument('dbname', required=False)
@click.argument('params', nargs=-1)
@pass_config
def psql(config, dbname, params):
    dbname = dbname or config.dbname
    os.environ['DOCKER_MACHINE'] = "1"
    conn = config.get_odoo_conn().clone(dbname=dbname)
    return _psql(conn, params)

def _psql(conn, params):
    dbname = conn.dbname
    if not dbname and len(params) == 1:
        if params[0] in ['template1', dbname]:
            dbname = params[0]
            params = []
    params = " ".join(params)
    psql_args = ['-h', conn.host, '-p', str(conn.port), '-U', conn.user]
    try:
        cmd = [
            'postgres',
            'psql',
        ]
        cmd += psql_args
        cmd += [
            dbname,
        ]

        __dcrun(cmd, interactive=True, env={
            "PGPASSWORD": conn.pwd,
        })
    finally:
        os.environ['PGPASSWORD'] = ""

@db.command(name='reset-odoo-db')
@click.argument('dbname', required=False)
@pass_config
@click.pass_context
def reset_db(ctx, config, dbname):
    dbname = dbname or config.dbname
    if not dbname:
        raise Exception("dbname required")
    __start_postgres_and_wait(config)
    conn = config.get_odoo_conn().clone(dbname=dbname)
    _dropdb(config, conn)
    conn = config.get_odoo_conn().clone(dbname='template1')
    __execute_sql(
        conn,
        "create database {}".format(
            dbname
        ),
        notransaction=True
    )

    # since odoo version 12 "-i base -d <name>" is required
    Commands.invoke(
        ctx,
        'update',
        module=['base'],
        no_restart=True,
        no_dangling_check=True,
        no_update_module_list=True,
        non_interactive=True,
    )

@db.command(name='setname')
@click.argument("DBNAME", required=True)
@click.pass_context
def set_db_name(ctx, DBNAME):
    Commands.invoke(ctx, 'set_setting', key="DBNAME", value=DBNAME)

@db.command(name='set-ownership')
@pass_config
def set_db_ownership(config):
    __set_db_ownership(config)

def __collect_other_turndb2dev_sql():
    from .odoo_config import customs_dir
    dir = customs_dir() / 'devscripts'
    if not dir.exists():
        return ""
    sqls = []
    for file in dir.glob("**/*.sql"):
        sqls.append(file.read_text())
    return "\n\n".join(sqls)

def __turn_into_devdb(conn):
    from .odoo_config import current_version
    from . import MyConfigParser
    myconfig = MyConfigParser(files['settings'])
    env = dict(map(lambda k: (k, myconfig.get(k)), myconfig.keys()))

    # encrypt password
    env['DEFAULT_DEV_PASSWORD'] = __hash_odoo_password(env['DEFAULT_DEV_PASSWORD'])

    sql_file = dirs['images'] / 'odoo' / 'config' / str(current_version()) / 'turndb2dev.sql'
    sql = sql_file.read_text()

    sql += __collect_other_turndb2dev_sql() or ""

    sql = __replace_all_envs_in_str(sql, env)

    critical = False
    for line in sql.split("\n"):
        if not line:
            continue
        if line.startswith("--set critical"):
            critical = True
            continue
        elif line.startswith("--set not-critical"):
            critical = False
            continue

        comment = re.findall(r'\/\*[^\*^\/]*\*\/', line)
        if comment:

            def ignore_line(comment):
                comment = comment[2:-2]
                if 'if-table-exists' in comment:
                    table = comment.split("if-table-exists")[1].strip()
                    res = __execute_sql(
                        conn,
                        "select count(*) from information_schema.tables where table_schema='public' and table_name='{}'".format(table),
                        fetchone=True
                    )
                    return not res[0]
                return False

            if any(list(ignore_line(comment) for comment in comment[0].split(";"))):
                continue
        try:
            print(line)
            __execute_sql(conn, line)
        except Exception:
            if critical:
                raise
            msg = traceback.format_exc()
            print("failed un-critical sql:", msg)

    remove_webassets(conn)


Commands.register(reset_db, 'reset-db')
