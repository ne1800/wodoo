import platform
import sys
import shutil
import hashlib
import os
import tempfile
import click
from .tools import __assert_file_exists
from .tools import __safe_filename
from .tools import __read_file
from .tools import __write_file
from .tools import __append_line
from .tools import _sanity_check
from .tools import _askcontinue
from .tools import __get_odoo_commit
from .tools import __is_container_running
from .tools import _get_bash_for_machine
from .tools import __cmd_interactive
from .tools import _display_machine_tips
from .tools import __start_postgres_and_wait
from .tools import __replace_in_file
from .tools import __wait_for_port
from .tools import __set_db_ownership
from .tools import __dcexec
from .tools import _get_machines
from .tools import __dc
from . import cli, pass_config, dirs, files, Commands
from .lib_clickhelpers import AliasedGroup

@cli.group(cls=AliasedGroup)
@pass_config
def control(config):
    pass


@control.command()
@click.option("-B", "--nobuild", is_flag=True)
@pass_config
@click.pass_context
def dev(ctx, config, nobuild):
    """
    starts developing in the odoo container
    """
    if not config.devmode:
        click.echo("Requires dev mode.")
        sys.exit(-1)
    ctx.invoke(kill, brutal=True)
    ctx.invoke(rm)
    Commands.invoke(ctx, 'reload')
    if not nobuild:
        ctx.invoke(build)
    click.echo("Killing all docker containers")
    os.system("docker kill $(docker ps -q)")
    __dc(['up', '-d'])
    Commands.invoke(ctx, 'kill', machines=["odoo"])
    if platform.system() in ["Windows", "Darwin"]:
        if config.run_fssync and config.fssync_host != '127.0.0.1':
            Commands.invoke(ctx, 'fssync_config')
        Commands.invoke(ctx, 'fssync_start')
    Commands.invoke(ctx, 'debug', machine="odoo")

@control.command(name='exec')
@click.argument('machine', required=True)
@click.argument('args', nargs=-1)
def execute(machine, args):
    args = [machine] + list(args)
    __dcexec(args)

@control.command()
@click.argument('machines', nargs=-1)
@click.option('-b', '--brutal', is_flag=True, help='dont wait')
@pass_config
@click.pass_context
def kill(ctx, config, machines, brutal=False):
    """
    kills running machine
    safely shutdowns postgres and redis

    if not brutal it means softly
    """
    from . import SAFE_KILL
    machines = list(machines)
    if config.run_postgres_in_ram and not machines:
        machines = list(filter(lambda x: x != 'postgres', _get_machines()))
    if not brutal and not config.devmode:
        safe_stop = []
        for machine in SAFE_KILL:
            if not machines or machine in machines:
                if __is_container_running(machine):
                    safe_stop += [machine]

        if safe_stop:
            __dc(["stop", "-t 20"] + safe_stop)  # persist data
    if config.devmode:
        __dc(['kill'] + list(machines))
    else:
        __dc(['stop', '-t 2'] + list(machines))

@control.command()
@click.pass_context
def force_kill(ctx, machine):
    ctx.invoke(kill, machine=machine, brutal=True)

@control.command()
@pass_config
def wait_for_container_postgres(config):
    __start_postgres_and_wait(config)

@control.command()
def wait_for_port(host, port):
    port = int(port)
    __wait_for_port(host=host, port=port)


@control.command()
@click.argument('machines', nargs=-1)
@pass_config
@click.pass_context
def recreate(ctx, config, machines):
    machines = list(machines)
    if not machines and 'postgres' not in machines:
        if config.run_postgres_in_ram:
            machines = list(filter(lambda x: x != 'postgres', _get_machines()))

    __dc(['up', '--no-start', '--force-recreate'] + machines)

@control.command()
@click.argument('machines', nargs=-1)
@click.option('-d', '--daemon', is_flag=True)
@pass_config
@click.pass_context
def up(ctx, config, machines, daemon):
    _sanity_check(config)
    machines = list(machines)
    if not machines or 'postgres' in machines:
        __set_db_ownership(config)

    if not machines and 'postgres' not in machines:
        if config.run_postgres_in_ram:
            machines = list(filter(lambda x: x != 'postgres', _get_machines()))

    options = [
    ]
    if daemon:
        options += ['-d']
    __dc(['up'] + options + machines)
    ctx.invoke(proxy_reload)

@control.command()
@click.argument('machines', nargs=-1)
@pass_config
@click.pass_context
def stop(ctx, config,  machines):
    ctx.invoke(kill, machines=machines)

@control.command()
@click.argument('machines', nargs=-1)
@pass_config
@click.pass_context
def rebuild(ctx, config, machines):
    Commands.invoke(ctx, 'compose', customs=config.customs)
    ctx.invoke(build, machines=machines, no_cache=True)

@control.command()
@click.argument('machines', nargs=-1)
@pass_config
@click.pass_context
def restart(ctx, config, machines):
    machines = list(machines)
    if not machines and 'postgres' not in machines:
        if config.run_postgres_in_ram:
            machines = list(filter(lambda x: x != 'postgres', _get_machines()))

    ctx.invoke(kill, machines=machines)
    ctx.invoke(up, machines=machines, daemon=True)
    ctx.invoke(proxy_reload)

@control.command()
@click.argument('machines', nargs=-1)
@pass_config
@click.pass_context
def rm(ctx, config, machines):
    machines = list(machines)
    if not machines and 'postgres' not in machines:
        if config.run_postgres_in_ram:
            machines = list(filter(lambda x: x != 'postgres', _get_machines()))
    __dc(['rm', '-f'] + machines)

@control.command()
@click.argument('machine', required=True)
def attach(machine):
    """
    attaches to running machine
    """
    _display_machine_tips(machine)
    bash = _get_bash_for_machine(machine)
    __cmd_interactive('exec', machine, bash)

@control.command()
@click.argument('machines', nargs=-1)
@click.option('--no-cache', is_flag=True)
@click.option('--pull', is_flag=False)
@pass_config
def build(config, machines, pull=False, no_cache=False):
    """
    no parameter all machines, first parameter machine name and passes other params; e.g. ./odoo build asterisk --no-cache"
    """
    options = []
    if pull:
        options += ['--pull']
    if no_cache:
        options += ['--no-cache']

    __dc(['build'] + options + list(machines), env={
        'ODOO_VERSION': config.odoo_version
    })

@control.command()
@click.argument('machine', required=True)
@click.option('-p', '--ports', is_flag=True, help='With Port 33824')
@pass_config
@click.pass_context
def debug(ctx, config, machine, ports):
    """
    starts /bin/bash for just that machine and connects to it; if machine is down, it is powered up; if it is up, it is restarted; as command an endless bash loop is set"
    """
    from . import commands

    # puts endless loop into container command and then attaches to it;
    # by this, name resolution to the container still works
    __set_db_ownership(config)
    if not config.devmode:
        _askcontinue(config, "Current machine {} is dropped and restartet with service ports in bash. Usually you have to type /debug.sh then.".format(machine))
    # shutdown current machine and start via run and port-mappings the replacement machine
    ctx.invoke(kill, machines=[machine])
    ctx.invoke(rm, machines=[machine])
    src_files = [files['debugging_template_onlyloop']]
    if ports:
        src_files += [files['debugging_template_withports']]

    for i, filepath in enumerate(src_files):
        dest = files['debugging_composer']
        dest = dest.parent / dest.name.replace(".yml", ".{}.yml".format(i))
        shutil.copy(filepath, dest)
        __replace_in_file(dest, "${CUSTOMS}", config.customs)
        __replace_in_file(dest, "${NAME}", machine)

        # TODO make configurable in machines
        PORT = str({
            'odoo': 8069,
            'odoo_debug': 8069
        }.get(machine, 80))
        __replace_in_file(dest, "{machine_main_port}", PORT)

        commands['dc'] += ['-f', dest]

    __dc(['up', '-d', machine])
    ctx.invoke(attach, machine=machine)

@control.command()
def proxy_reload():
    pass
    # if __is_container_running('proxy'):
    #    __dcexec(['proxy', '/opt/bin/hot_reload.sh'])


Commands.register(kill)
Commands.register(up)
Commands.register(wait_for_container_postgres)
Commands.register(build)
Commands.register(rm)
Commands.register(recreate)
Commands.register(proxy_reload)
Commands.register(debug)
Commands.register(restart)