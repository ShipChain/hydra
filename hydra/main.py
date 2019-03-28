import os
import warnings

from cement import App, TestApp, init_defaults
from cement.core.exc import CaughtSignal

from .controllers.base import Base
from .controllers.client import Client
from .controllers.devel import Devel
from .controllers.network import Network
from .core.exc import HydraError
from .helpers import UtilsHelper
from .helpers.client import ClientHelper
from .helpers.devel import DevelHelper
from .helpers.networks import NetworksHelper
from .helpers.release import ReleaseHelper

# configuration defaults
CONFIG = init_defaults('hydra', 'log.logging', 'release',
                       'devel', 'provision', 'client')
CONFIG['hydra']['workdir'] = os.path.realpath(os.getcwd())
CONFIG['hydra']['project'] = 'shipchain'
CONFIG['hydra']['binary_name'] = '%(project)s'
CONFIG['hydra']['project_source'] = 'https://github.com/shipchain/hydra.git'
CONFIG['hydra']['channel_url'] = 'https://shipchain-network-dist.s3.amazonaws.com'
CONFIG['log.logging']['level'] = 'debug'
CONFIG['release']['distdir'] = './dist'
CONFIG['release']['build_binary_path'] = './loomchain/shipchain'
CONFIG['release']['aws_profile'] = None
CONFIG['release']['aws_s3_dist_bucket'] = 'shipchain-network-dist'
CONFIG['provision']['aws_profile'] = None
CONFIG['provision']['aws_ec2_region'] = 'us-east-1'
CONFIG['provision']['aws_ec2_instance_type'] = 'm5.xlarge'
CONFIG['provision']['aws_ec2_ami_id'] = 'ami-0a313d6098716f372'
CONFIG['provision']['pip_install'] = 'git+%(project_source)s@master'
CONFIG['devel']['path'] = '%(workdir)s/devel'
CONFIG['client']['pip_install'] = 'git+%(project_source)s@master'


def add_helpers(app):
    UtilsHelper.attach('utils', app)
    ReleaseHelper.attach('release', app)
    DevelHelper.attach('devel', app)
    ClientHelper.attach('client', app)
    NetworksHelper.attach('networks', app)
    app.project = app.config.get('hydra', 'project')


class Hydra(App):
    """ShipChain Network Hydra Manager primary application."""

    class Meta:
        label = 'hydra'

        # configuration defaults
        config_defaults = CONFIG

        # call sys.exit() on close
        close_on_exit = True

        # load additional framework extensions
        extensions = [
            'yaml',
            'colorlog',
            'jinja2',
        ]

        # configuration handler
        config_handler = 'yaml'

        # configuration file suffix
        config_file_suffix = '.yml'

        # set the log handler
        log_handler = 'colorlog'

        # set the output handler
        output_handler = 'jinja2'

        # register handlers
        handlers = [
            Base,
            Devel,
            Network,
            Client
        ]

        hooks = [
            ('post_setup', add_helpers)
        ]


class HydraTest(TestApp, Hydra):
    """A sub-class of Hydra that is better suited for testing."""

    class Meta:
        label = 'hydra'


def main():
    with Hydra() as app:
        app.config_file = os.path.expanduser('~/.hydra.yml')  # pylint: disable=attribute-defined-outside-init

        if not os.path.exists(app.config_file):
            print('First run: Generating ~/.hydra.yml config...')
            from yaml import dump
            open(app.config_file, 'w+').write(
                dump(CONFIG, indent=4, default_flow_style=False))
        try:
            app.run()

        except AssertionError as exc:
            print(f'AssertionError > {exc.args[0]}')
            app.exit_code = 1

            if app.debug is True:
                import traceback
                traceback.print_exc()

        except HydraError as exc:
            print(f'HydraError > {exc.args[0]}')
            app.exit_code = 1

            if app.debug is True:
                import traceback
                traceback.print_exc()

        except CaughtSignal as exc:
            # Default Cement signals are SIGINT and SIGTERM, exit 0 (non-error)
            print(f'\n{exc}')
            app.exit_code = 0


if __name__ == '__main__':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        main()
