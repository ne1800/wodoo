import sys
import subprocess
import inquirer
import traceback
from datetime import datetime
import time
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
from .tools import _exists_table
from .tools import __get_odoo_commit
from .tools import _start_postgres_and_wait
from .tools import __cmd_interactive
from .tools import __get_installed_modules
from . import cli, pass_config, dirs, files, Commands
from .lib_clickhelpers import AliasedGroup
from .tools import _execute_sql

class UpdateException(Exception): pass

@cli.group(cls=AliasedGroup)
@pass_config
def odoo_module(config):
    pass

@odoo_module.command(name='abort-upgrade')
@pass_config
def abort_upgrade(config):
    click.echo("Aborting upgrade...")
    SQL = """
        UPDATE ir_module_module SET state = 'installed' WHERE state = 'to upgrade';
        UPDATE ir_module_module SET state = 'uninstalled' WHERE state = 'to install';
    """
    _execute_sql(config.get_odoo_conn(), SQL)

def _get_default_modules_to_update():
    from .module_tools import Modules, DBModules
    mods = Modules()
    module = mods.get_customs_modules('to_update')
    module += DBModules.get_uninstalled_modules_where_others_depend_on()
    return module

@odoo_module.command(name='update-ast-file')
def update_ast_file():
    from .odoo_parser import update_cache
    update_cache()

@odoo_module.command(name='update-module-file')
@click.argument('module', nargs=-1, required=True)
def update_module_file(module):
    from .module_tools import Module
    for module in module:
        Module.get_by_name(module).update_module_file()

@odoo_module.command(name='run-tests')
@pass_config
@click.pass_context
def run_tests(ctx, config):
    if not config.devmode:
        click.secho("Devmode required to run unit tests. Database will be destroyed.", fg='red')
        sys.exit(-1)

    if not config.force:
        click.secho("Please provide parameter -f - database will be dropped.\n\nodoo -f run-tests", fg='red')
        sys.exit(-1)

    from .odoo_config import MANIFEST
    tests = MANIFEST().get('tests', [])
    if not tests:
        click.secho("No test files found!")
        return
    Commands.invoke(ctx, 'wait_for_container_postgres', missing_ok=True)

    config.force = True
    Commands.invoke(ctx, 'reset-db')
    update.invoke(ctx, config, "", tests=False)
    for module in tests:
        update.invoke(ctx, config, module, tests=True)


@odoo_module.command()
@click.argument('module', nargs=-1, required=False)
@click.option('--installed-modules', '-i', default=False, is_flag=True, help="Updates only installed modules")
@click.option('--dangling-modules', '-d', default=False, is_flag=True, help="Updates only dangling modules")
@click.option('--no-update-module-list', '-n', default=False, is_flag=True, help="Does not install/update module list module")
@click.option('--non-interactive', '-I', default=True, is_flag=True, help="Not interactive")
@click.option('--check-install-state', default=True, is_flag=True, help="Check for dangling modules afterwards")
@click.option('--no-restart', default=False, is_flag=True, help="If set, no machines are restarted afterwards")
@click.option('--no-dangling-check', default=False, is_flag=True, help="Not checking for dangling modules")
@click.option('--tests', default=False, is_flag=True, help="Runs tests")
@click.option('--i18n', default=False, is_flag=True, help="Overwrite Translations")
@pass_config
@click.pass_context
def update(ctx, config, module, dangling_modules, installed_modules, non_interactive, no_update_module_list, no_dangling_check=False, check_install_state=True, no_restart=True, i18n=False, tests=False):
    """
    Just custom modules are updated, never the base modules (e.g. prohibits adding old stock-locations)
    Minimal downtime;

    To update all (custom) modules set "all" here
    """
    from .module_tools import Modules, DBModules
    # ctx.invoke(module_link)
    Commands.invoke(ctx, 'wait_for_container_postgres', missing_ok=True)
    module = list(filter(lambda x: x, sum(map(lambda x: x.split(','), module), [])))  # '1,2 3' --> ['1', '2', '3']

    if not no_restart:
        Commands.invoke(ctx, 'kill', machines=[
            'odoo',
            'odoo_queuejobs',
            'odoo_cronjobs',
        ])
        Commands.invoke(ctx, 'up', machines=['redis'], daemon=True)
        Commands.invoke(ctx, 'wait_for_container_postgres')

    if not module:
        module = _get_default_modules_to_update()

    if not no_dangling_check:
        if any(x[1] == 'uninstallable' for x in DBModules.get_dangling_modules()):
            for x in DBModules.get_dangling_modules():
                click.echo("{}: {}".format(*x[:2]))
            if non_interactive or input("Uninstallable modules found - shall I set them to 'uninstalled'? [y/N]").lower() == 'y':
                _execute_sql(config.get_odoo_conn(), "update ir_module_module set state = 'uninstalled' where state = 'uninstallable';")
        if DBModules.get_dangling_modules() and not dangling_modules:
            if not no_dangling_check:
                Commands.invoke(ctx, 'show_install_state', suppress_error=True)
                input("Abort old upgrade and continue? (Ctrl+c to break)")
                ctx.invoke(abort_upgrade)
    if installed_modules:
        module += __get_installed_modules(config)
    if dangling_modules:
        module += [x[0] for x in DBModules.get_dangling_modules()]
    module = list(filter(lambda x: x, module))
    if not module:
        raise Exception("no modules to update")

    click.echo("Run module update")
    if config.odoo_update_start_notification_touch_file_in_container:
        with open(config.odoo_update_start_notification_touch_file_in_container, 'w') as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        params = [','.join(module)]
        if non_interactive:
            params += ['--non-interactive']
        if not no_update_module_list:
            params += ['--no-update-modulelist']
        if no_dangling_check:
            params += ['no-dangling-check']
        if i18n:
            params += ['--i18n']
        if not tests:
            params += ['--no-tests']
        rc = _exec_update(config, params)
        if not rc:
            raise UpdateException(module)

    except UpdateException:
        raise
    except Exception:
        click.echo(traceback.format_exc())
        ctx.invoke(show_install_state, suppress_error=True)
        raise Exception("Error at /update_modules.py - aborting update process.")

    if check_install_state:
        ctx.invoke(show_install_state, suppress_error=no_dangling_check)

    if not no_restart and config.use_docker:
        Commands.invoke(ctx, 'restart', machines=['odoo'])
        if config.run_odoocronjobs:
            Commands.invoke(ctx, 'restart', machines=['odoo_cronjobs'])
        if config.run_queuejobs:
            Commands.invoke(ctx, 'restart', machines=['odoo_queuejobs'])
        Commands.invoke(ctx, 'up', daemon=True)

    Commands.invoke(ctx, 'status')
    if config.odoo_update_start_notification_touch_file_in_container:
        with open(config.odoo_update_start_notification_touch_file_in_container, 'w') as f:
            f.write("0")

@odoo_module.command(name="update-i18n", help="Just update translations")
@click.argument('module', nargs=-1, required=False)
@click.option('--no-restart', default=False, is_flag=True, help="If set, no machines are restarted afterwards")
@pass_config
@click.pass_context
def update_i18n(ctx, config, module, no_restart):
    Commands.invoke(ctx, 'wait_for_container_postgres')
    module = list(filter(lambda x: x, sum(map(lambda x: x.split(','), module), [])))  # '1,2 3' --> ['1', '2', '3']

    if not module:
        module = _get_default_modules_to_update()

    try:
        params = [','.join(module)]
        params += ['--non-interactive']
        params += ['--no-update-modulelist']
        params += ['no-dangling-check']
        params += ['--only-i18n']
        _exec_update(config, params)
    except Exception:
        click.echo(traceback.format_exc())
        ctx.invoke(show_install_state, suppress_error=True)
        raise Exception("Error at /update_modules.py - aborting update process.")

    if not no_restart:
        Commands.invoke(ctx, 'restart', machines=['odoo'])

@odoo_module.command(name='remove-old')
@click.option("--ask-confirm", default=True, is_flag=True)
@pass_config
@click.pass_context
def remove_old_modules(ctx, config, ask_confirm=True):
    """
    Sets modules to 'uninstalled', that have no module dir anymore.
    """
    from .module_tools import get_manifest_path_of_module_path
    from .odoo_config import get_odoo_addons_paths
    click.echo("Analyzing which modules to remove...")
    Commands.invoke(ctx, 'wait_for_container_postgres')
    mods = sorted(map(lambda x: x[0], _execute_sql(config.get_odoo_conn(), "select name from ir_module_module where state in ('installed', 'to install', 'to upgrade') or auto_install = true;", fetchall=True)))
    mods = list(filter(lambda x: x not in ('base'), mods))
    to_remove = []
    for mod in mods:
        for path in get_odoo_addons_paths():
            if get_manifest_path_of_module_path(path / mod):
                break
        else:
            to_remove.append(mod)
    if not to_remove:
        click.echo("Nothing found to remove")
        return
    click.echo("Following modules are set to uninstalled:")
    for mod in to_remove:
        click.echo(mod)
    if ask_confirm:
        answer = inquirer.prompt([inquirer.Confirm('confirm', message="Continue?", default=True)])
        if not answer or not answer['confirm']:
            return
    for mod in to_remove:
        _execute_sql(config.get_odoo_conn(), "update ir_module_module set auto_install=false, state = 'uninstalled' where name = '{}'".format(mod))
        click.echo("Set module {} to uninstalled.".format(mod))

@odoo_module.command()
@pass_config
def progress(config):
    """
    Displays installation progress
    """
    for row in _execute_sql(config.get_odoo_conn(), "select state, count(*) from ir_module_module group by state;", fetchall=True):
        click.echo("{}: {}".format(row[0], row[1]))

@odoo_module.command(name='show-install-state')
@pass_config
def show_install_state(config, suppress_error=False):
    from .module_tools import DBModules
    dangling = DBModules.get_dangling_modules()
    if dangling:
        click.echo("Displaying dangling modules:")
    for row in dangling:
        click.echo("{}: {}".format(row[0], row[1]))

    if dangling and not suppress_error:
        raise Exception("Dangling modules detected - please fix installation problems and retry!")

@odoo_module.command(name='show-addons-paths')
def show_addons_paths():
    from .odoo_config import get_odoo_addons_paths
    paths = get_odoo_addons_paths()
    for path in paths:
        click.echo(path)

@odoo_module.command(name='pretty-print-manifest')
def pretty_print_manifest():
    from .odoo_config import MANIFEST
    MANIFEST().rewrite()

@odoo_module.command(name='show-conflicting-modules')
def show_conflicting_modules():
    from .odoo_config import get_odoo_addons_paths
    get_odoo_addons_paths(show_conflicts=True)

def _exec_update(config, params):
    if config.use_docker:
        params = ['run', 'odoo_update', '/update_modules.py'] + params
        return __cmd_interactive(*params)
    else:
        from . import lib_control_native
        return lib_control_native._update_command(params)


Commands.register(progress)
Commands.register(remove_old_modules)
Commands.register(update)
Commands.register(show_install_state)
