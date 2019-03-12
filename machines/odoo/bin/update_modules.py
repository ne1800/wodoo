#!/usr/bin/python3
import os
import datetime
import sys
import tempfile
import subprocess
from time import sleep
from module_tools import odoo_config
from module_tools import odoo_parser
from module_tools.module_tools import get_all_langs
from module_tools.module_tools import delete_qweb
from module_tools.module_tools import check_if_all_modules_from_install_are_installed
from module_tools.module_tools import is_module_installed
from module_tools.module_tools import is_module_listed
from module_tools.module_tools import get_lang_file_of_module
from module_tools.module_tools import get_uninstalled_modules_that_are_auto_install_and_should_be_installed # NOQA
from module_tools.odoo_parser import manifest2dict
from utils import get_env # NOQA

INTERACTIVE = not any(x == '--non-interactive' for x in sys.argv)
NO_UPDATE_MODULELIST = any(x == '--no-update-modulelist' for x in sys.argv)
PARAMS = [x for x in sys.argv[1:] if not x.startswith("-")]
I18N_OVERWRITE = [x for x in sys.argv[1:] if x.strip().startswith("--i18n")]
DELETE_QWEB = [x for x in sys.argv[1:] if x.strip().startswith("--delete-qweb")]
RUN_TESTS = [x for x in sys.argv[1:] if x.strip().startswith("--run-tests")]

def _get_uninstalled_modules_that_are_auto_install_and_should_be_installed():
    modules = []
    modules += get_uninstalled_modules_that_are_auto_install_and_should_be_installed()
    return sorted(list(set(modules)))

def update(mode, module):
    assert mode in ['i', 'u']
    assert module
    assert isinstance(module, str)

    if module == 'all':
        raise Exception("update 'all' not allowed")

    if RUN_TESTS:
        if mode == "i":
            TESTS = '' # dont run tests at install
        else:
            TESTS = '--test-enable'
    else:
        TESTS = ''

    print(mode, module)
    params = [
        '/usr/bin/sudo',
        '-H',
        '-u',
        os.getenv("ODOO_USER"),
        os.path.expandvars("$SERVER_DIR/{}".format(get_env()["ODOO_EXECUTABLE"])),
        '-c',
        os.path.expandvars("$CONFIG_DIR/config_openerp"),
        '-d',
        os.path.expandvars("$DBNAME"),
        '-' + mode,
        module,
        '--stop-after-init',
        '--log-level=debug',
    ]
    if TESTS:
        params += [TESTS]
    subprocess.check_call(params)

    if mode == 'i':
        for module in module.split(','):
            if not is_module_installed(module):
                print("{} is not installed - but it was tried to be installed.".format(module))
                sys.exit(1)
    elif I18N_OVERWRITE:
        for module in module.split(','):
            if is_module_installed(module):
                for lang in get_all_langs():
                    if lang == 'en_US':
                        continue
                    lang_file = get_lang_file_of_module(lang, module)
                    if not lang_file:
                        continue
                    if os.path.isfile(lang_file):
                        print("Updating language {} for module {}:".format(lang, module))
                        params = [
                            '/usr/bin/sudo',
                            '-H',
                            '-u',
                            os.getenv("ODOO_USER"),
                            os.path.expandvars("$SERVER_DIR/{}".format(get_env()["ODOO_EXECUTABLE"])),
                            '-c',
                            os.path.expandvars("$CONFIG_DIR/config_openerp"),
                            '-d',
                            os.path.expandvars("$DBNAME"),
                            '-l',
                            lang,
                            '--i18n-import={}/i18n/{}.po'.format(module, lang),
                            '--i18n-overwrite',
                        ]
                        subprocess.check_call(params)

    print(mode, module, 'done')

def update_module_list():
    MOD = "update_module_list"
    if not is_module_installed(MOD):
        print("Update Module List is not installed - installing it...")
        update('i', MOD)

    if not is_module_installed(MOD):
        print("")
        print("")
        print("")
        print("Severe update error - module 'update_module_list' not installable, but is required.")
        print("")
        print("Try to manually start odoo and click on 'Module Update' and install this by hand.")
        print("")
        print("")
        sys.exit(82)
    update('u', MOD)


def all_dependencies_installed(module):
    dir = odoo_config.module_dir(module)
    if not dir:
        raise Exception("Path to {} does not exist. Perhaps ./odoo link required?")
    manifest_path = odoo_parser.get_manifest_file(dir)
    manifest = manifest2dict(manifest_path)
    return all(is_module_installed(mod) for mod in manifest.get('depends', []))

def main():
    MODULE = PARAMS[0] if PARAMS else ""
    single_module = MODULE and ',' not in MODULE

    print("--------------------------------------------------------------------------")
    print("Updating Module {}".format(MODULE))
    print("--------------------------------------------------------------------------")

    if MODULE == 'all':
        MODULE = ''

    if not MODULE:
        raise Exception("requires module!")

    subprocess.check_call([
        'bash',
        '-c',
        'source /eval_odoo_settings.sh; /apply-env-to-config.py'
    ])

    summary = []

    for module in MODULE.split(','):
        if not is_module_installed(module):
            if not is_module_listed(module):
                update_module_list()
                if not is_module_listed(module):
                    raise Exception("After updating module list, module was not found: {}".format(module))
            update('i', module)
            summary.append("INSTALL " + module)

    if DELETE_QWEB:
        for module in MODULE.split(','):
            print("Deleting qweb of module {}".format(module))
            delete_qweb(module)
    update('u', MODULE)
    for module in MODULE.split(","):
        summary.append("UPDATE " + module)

    # check if at auto installed modules all predecessors are now installed; then install them
    if not single_module:
        auto_install_modules = _get_uninstalled_modules_that_are_auto_install_and_should_be_installed()
        if auto_install_modules:
            print("Going to install following modules, that are auto installable modules")
            print(','.join(auto_install_modules))
            print("")
            if INTERACTIVE:
                input("You should press Ctrl+C NOW to abort")
            update('i', ','.join(auto_install_modules))

    print("--------------------------------------------------------------------------------")
    print("Summary of update module")
    print("--------------------------------------------------------------------------------")
    for line in summary:
        print(line)

    if not single_module:
        check_if_all_modules_from_install_are_installed()


if __name__ == '__main__':
    main()
