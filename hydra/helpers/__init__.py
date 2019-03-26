import os
import subprocess
import sys

import libtmux
from colored import fg, attr
from pyfiglet import Figlet

FIG = lambda t, f='slant': Figlet(font=f).renderText(t)
RESET = attr('reset')
ORANGE = fg('orange_1')
SHIP = ORANGE + FIG('ShipChain') + RESET
BLUE = fg('blue')
HYDRA = BLUE + FIG('HYDRA', 'block') + RESET


class HydraHelper(object):
    def __init__(self, app):
        self.app = app

    @classmethod
    def attach(kls, name, app):
        setattr(app, name, kls(app))

    @property
    def config(self):
        return self.app.config


class UtilsHelper(HydraHelper):
    def env_or_arg(self, arg_name, env_name, or_path=None, required=False):
        if getattr(self.app.pargs, arg_name):
            return getattr(self.app.pargs, arg_name)
        elif (env_name in os.environ):
            self.app.log.info('--app not specified, using environ[%s]: %s' % (env_name, os.environ[env_name]))
            return os.environ[env_name]

        elif or_path and os.path.exists(self.app.utils.path(or_path)):
            with open(self.app.utils.path(or_path), 'r') as fh:
                value = fh.read().strip()
                self.app.log.info('--app not specified, using file %s: %s' % (or_path, value))
                return value
        elif required:
            self.app.log.error('You must specify either --%s or set %s in your environment' % (arg_name, env_name))
            sys.exit()

    def workdir(self, *extrapath):
        return os.path.realpath(os.path.join(
            self.config['hydra']['workdir'],
            *extrapath
        ))

    def path(self, *extrapath):
        return self.workdir(*[e % self.config['hydra'] for e in extrapath])

    @property
    def binary_name(self):
        return self.config['hydra']['binary_name'] % self.config['hydra']

    def binary_exec(self, path, *cmd):
        return self.raw_exec(path, *cmd)

    def raw_exec(self, *cmd):
        return subprocess.run(cmd, encoding='utf-8',
                              stderr=subprocess.PIPE, stdout=subprocess.PIPE)

    def run_in_tmux(self, session, window, strcmd, pane=None, kill_session=True, attach=False):
        server = libtmux.Server()
        server.new_session(session,
                           window_name=window,
                           kill_session=kill_session,
                           attach=attach,
                           window_command=strcmd)

    def download_file(self, destination, url):
        import requests
        self.app.log.debug('Downloading: %s from %s' % (destination, url))
        open(destination, 'wb+').write(requests.get(url).content)

    def download_release_file(self, destination, file, version=None):
        host = self.config.get('hydra', 'channel_url')
        if not version:
            url = '%s/latest/%s' % (host, file)
        else:
            url = '%s/archive/%s/%s' % (host, version, file)
        return self.download_file(destination, url)

    def get_binary_version(self, path):
        if not os.path.exists(path):
            raise IOError('Expected shipchain binary:', path)
        return self.binary_exec(path, 'version').stderr.split('\n')[0]
