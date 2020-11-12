import subprocess
from git import Repo
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
from .tools import remove_webassets
from .tools import _askcontinue
from .tools import get_volume_names
from . import cli, pass_config, Commands
from .lib_clickhelpers import AliasedGroup
from .tools import __hash_odoo_password
from .tools import _remove_postgres_connections, _execute_sql

def __get_snapshot_db(config):
    d = config.files['run/snapshot_mappings.txt']
    if not d.exists():
        __set_snapshot_db(config, {})
    data = yaml.safe_load(d.read_text())
    # filter out non existing snapshots
    if config.use_docker:
        from . import lib_db_snapshots_docker_btrfs as snaps
        snapshots = snaps.__get_snapshots(config)
        data = {x: y for (x, y) in data.items() if x in snapshots}
    else:
        raise NotImplementedError("")
    return data

def __set_snapshot_db(config, values):
    d = config.files['run/snapshot_mappings.txt']
    d.write_text(yaml.dump(values, default_flow_style=False))

@cli.group(cls=AliasedGroup)
@pass_config
def snapshot(config):
    config.__get_snapshot_db = __get_snapshot_db
    config.__set_snapshot_db = __set_snapshot_db
    config.__choose_snapshot = __choose_snapshot
    if config.use_docker:
        from . import lib_db_snapshots_docker_btrfs as snapshot_manager
    else:
        from . import lib_db_snapshots_plain_postgres as snapshot_manager
    config.snapshot_manager = snapshot_manager

def __choose_snapshot(config, take=False):
    snapshots = config.snapshot_manager.__get_snapshots(config)
    mappings = __get_snapshot_db(config)
    snapshots2 = []
    used_mappings = {}
    for x in snapshots:
        snap_name = mappings.get(x, x)
        if x != snap_name:
            d = config.snapshot_manager._try_get_date_from_snap(x)
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

    snapshot = inquirer.prompt([inquirer.List('snapshot', "", choices=snapshots2)])
    if not snapshot:
        sys.exit(0)
    snapshot = snapshot['snapshot']
    snapshot = used_mappings[snapshot]
    return snapshot


@snapshot.command(name="list")
@pass_config
def do_list(config):
    config.snapshot_manager.assert_environment(config)
    snapshots = config.snapshot_manager.__get_snapshots(config)
    mappings = __get_snapshot_db(config)

    for snap in snapshots:
        print(mappings.get(snap, snap))

@snapshot.command(name="save")
@click.argument('name', required=False)
@pass_config
def snapshot_make(config, name):
    config.snapshot_manager.assert_environment(config)
    if not name:
        repo = Repo(os.getcwd())
        name = repo.active_branch.name
        click.secho(f"Using {name} as snapshot name")

    # remove existing snaps
    values = config.__get_snapshot_db(config)
    for snapshot, snapname in list(values.items()):
        if snapname == name:
            config.snapshot_manager.remove(config, snapshot)
            del values[snapshot]
            config.__set_snapshot_db(config, values)
    snapshot = config.snapshot_manager.make_snapshot(config, name)
    if name:
        values = config.__get_snapshot_db(config)
        values[snapshot] = name
        config.__set_snapshot_db(config, values)
    click.echo("Made snapshot: {}".format(snapshot))

@snapshot.command(name="restore")
@click.argument('name', required=False)
@pass_config
@click.pass_context
def snapshot_restore(ctx, config, name):
    config.snapshot_manager.assert_environment(config)
    name = __choose_snapshot(config, take=name)
    if not name:
        return
    config.snapshot_manager.restore(config, name)

@snapshot.command(name="remove")
@click.argument('name', required=False)
@pass_config
@click.pass_context
def snapshot_remove(ctx, config, name):
    config.snapshot_manager.assert_environment(config)

    snapshot = __choose_snapshot(config, take=name)
    if not snapshot:
        return
    config.snapshot_manager.remove(config, snapshot)

@snapshot.command(name="clear", help="Removes all snapshots")
@pass_config
@click.pass_context
def snapshot_clear_all(ctx, config):
    config.snapshot_manager.assert_environment(config)

    snapshots = config.snapshot_manager.__get_snapshots(config)
    if snapshots:
        for snap in snapshots:
            config.snapshot_manager.remove(config, snap)
    ctx.invoke(do_list)
