
from cement import App, TestApp, init_defaults
from cement.core.exc import CaughtSignal
from .core.exc import HydraError
from .controllers.base import Base
from .controllers.devel import Devel
from .controllers.network import Network
from .controllers import hydra_utils
import os

# configuration defaults
CONFIG = init_defaults('hydra', 'log.logging', 'devel', 'provision')
CONFIG['hydra']['workdir'] = os.path.realpath(os.getcwd())
CONFIG['hydra']['distdir'] = './dist'
CONFIG['hydra']['build_binary_path'] = './loomchain/shipchain'
CONFIG['hydra']['dist_binary_path'] = '%(distdir)s/shipchain'
CONFIG['hydra']['aws_profile'] = 'shipchain'
CONFIG['hydra']['aws_s3_dist_bucket'] = '%(aws_profile)s-network-dist'
CONFIG['hydra']['aws_ec2_region'] = 'us-east-1'
CONFIG['hydra']['aws_ec2_instance_type'] = 'm5.xlarge'
CONFIG['hydra']['aws_ec2_ami_id'] = 'ami-0a313d6098716f372'
CONFIG['devel']['path'] = '%(workdir)s/devel'
CONFIG['provision']['hydra_source'] = 'hydra'

def add_helpers(app):
    hydra_utils.Utils.register('utils', app)
    hydra_utils.Release.register('release', app)
    hydra_utils.Devel.register('devel', app)
    hydra_utils.Troposphere.register('tropo', app)

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
            Network
        ]

        hooks = [
            ('post_setup', add_helpers)
        ]


class HydraTest(TestApp,Hydra):
    """A sub-class of Hydra that is better suited for testing."""

    class Meta:
        label = 'hydra'


def main():
    with Hydra() as app:
        try:
            app.run()

        except AssertionError as e:
            print('AssertionError > %s' % e.args[0])
            app.exit_code = 1

            if app.debug is True:
                import traceback
                traceback.print_exc()

        except HydraError as e:
            print('HydraError > %s' % e.args[0])
            app.exit_code = 1

            if app.debug is True:
                import traceback
                traceback.print_exc()

        except CaughtSignal as e:
            # Default Cement signals are SIGINT and SIGTERM, exit 0 (non-error)
            print('\n%s' % e)
            app.exit_code = 0


if __name__ == '__main__':
    main()
