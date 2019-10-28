import os
import subprocess
import urllib.parse

import libtmux
import requests
from colored import fg, attr
from pyfiglet import Figlet
from tqdm import tqdm

from hydra.core.exc import HydraError


def fig(text, font='slant'):
    return Figlet(font=font).renderText(text)


CHECK_SUCCESS = f"{fg('green')}✓{attr('reset')}"
CROSS_FAIL = f"{fg('red')}✗{attr('reset')}"
HYDRA = fg('blue') + fig('HYDRA', 'block') + attr('reset')


def inject_jinja_globals(outputs):
    return {
        'RESET': attr('reset'),
        'ORANGE': fg('orange_1'),
        'SHIP': fg('orange_1') + fig('ShipChain') + attr('reset'),
        'BLUE': fg('blue'),
        'HYDRA': HYDRA,
        'CHECK_SUCCESS': CHECK_SUCCESS,
        'CROSS_FAIL': CROSS_FAIL,
        'OUTPUTS': outputs
    }


class HydraHelper:
    def __init__(self, app):
        self.app = app

    @classmethod
    def attach(cls, name, app):
        setattr(app, name, cls(app))

    @property
    def config(self):
        return self.app.config


class UtilsHelper(HydraHelper):
    BOOLEAN_STATES = {'1': True, 'yes': True, 'true': True, 'on': True,
                      '0': False, 'no': False, 'false': False, 'off': False}

    def env_or_arg(self, arg_name, env_name, or_path=None, required=False):
        # pargs has default value of None if argument not provided
        value = getattr(self.app.pargs, arg_name)

        if value is None:
            if env_name in os.environ:
                value = os.environ[env_name]
                self.app.log.info(f'--{arg_name} not specified, using environ[{env_name}]: {value}')

            elif or_path and os.path.exists(self.app.utils.path(or_path)):
                with open(self.app.utils.path(or_path), 'r') as variable_file:
                    value = variable_file.read().strip()
                    self.app.log.info(f'--{arg_name} not specified, using file {or_path}: {value}')

        if required and value is None:
            self.app.log.error(f'You must specify either --{arg_name} or set {env_name} in your environment')
            raise HydraError(f'You must specify either --{arg_name} or set {env_name} in your environment')

        return value

    def workdir(self, *extra_path):
        return os.path.realpath(os.path.join(
            self.config['hydra']['workdir'],
            *extra_path
        ))

    def path(self, *extra_paths):
        return self.workdir(*[extra_path % self.config['hydra'] for extra_path in extra_paths])

    @property
    def binary_name(self):
        return self.config['hydra']['binary_name'] % self.config['hydra']

    def binary_exec(self, path, *cmd, **kwargs):
        return self.raw_exec(path, *cmd, **kwargs)

    def raw_exec(self, *cmd, **kwargs):  # pylint: disable=no-self-use
        """This provides subprocess functionality via the attached UtilsHelper instance.

        Pylint `no-self-use` should be disabled on this method to prevent that warning.
        This could be a staticmethod, but would make invoking it more cumbersome than it already is
        """
        execution_result = subprocess.run(cmd, encoding='utf-8', stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if 'ignore_error' not in kwargs or not kwargs['ignore_error']:
            execution_result.check_returncode()
        return execution_result

    def run_in_tmux(self, session, window, strcmd, **kwargs):  # pylint: disable=no-self-use
        """This provides tmux session functionality via the attached UtilsHelper instance.

        Pylint `no-self-use` should be disabled on this method to prevent that warning.
        This could be a staticmethod, but would make invoking it more cumbersome than it already is
        """
        kill_session = kwargs['kill_session'] if 'kill_session' in kwargs else True
        attach = kwargs['attach'] if 'attach' in kwargs else False

        server = libtmux.Server()
        server.new_session(session,
                           window_name=window,
                           kill_session=kill_session,
                           attach=attach,
                           window_command=strcmd)

    def download_file(self, destination, url):
        self.app.log.debug(f'Downloading: {destination} from {url}')
        open(destination, 'wb+').write(requests.get(url).content)

    def download_file_stream(self, destination, url, show_progress=True):
        """
        Download a file from a URL.  This supports
        :param destination:
        :param url:
        :return:
        """
        self.app.log.info(f'Downloading: {destination}')
        with requests.get(url, stream=True) as request_stream, open(destination, 'wb') as file_stream:
            total_bytes = request_stream.headers['Content-Length']
            self.app.log.debug(f'Retrieving {total_bytes} bytes from {url}')
            with TqdmProgressBar(unit='B', unit_scale=True, miniters=1, desc=destination) as progressbar:
                self._copyfileobj_progress(request_stream.raw, file_stream, total_bytes, progressbar)

    def _copyfileobj_progress(self, source_stream, destination_stream, total_bytes, progressbar, chunk_size=16 * 1024):
        """
        copy data from file-like object source_stream to file-like object destination_stream
        Borrowed from shutil.copyfileobj and modified for progressbar support
        """
        chunks_processed = 1
        while 1:
            buf = source_stream.read(chunk_size)
            if not buf:
                break
            destination_stream.write(buf)
            progressbar.update_to(chunks_processed, chunk_size, int(total_bytes))
            chunks_processed += 1

    def download_release_file(self, destination, file, version=None):
        host = self.config.get('hydra', 'channel_url')
        if not version or version == "latest":
            url = f'{host}/latest/{file}'
        else:
            version = urllib.parse.quote(version)
            url = f'{host}/archive/{version}/{file}'
        return self.download_file_stream(destination, url)

    def get_binary_version(self, path):
        if not os.path.exists(path):
            raise IOError('Expected shipchain binary:', path)
        return self.binary_exec(path, 'version').stderr.split('\n')[0]


class TqdmProgressBar(tqdm):
    """
    Provides `update_to(n)` which uses `tqdm.update(delta_n)`.
    Borrowed from TQDM Docs: https://github.com/tqdm/tqdm#hooks-and-callbacks
    """

    def update_to(self, b=1, bsize=1, tsize=None):
        """
        b  : int, optional
            Number of blocks transferred so far [default: 1].
        bsize  : int, optional
            Size of each block (in tqdm units) [default: 1].
        tsize  : int, optional
            Total size (in tqdm units). If [default: None] remains unchanged.
        """
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)  # will also set self.n = b * bsize
