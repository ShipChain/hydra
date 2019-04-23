import glob
import json
import os
from collections import OrderedDict
from datetime import datetime
from shutil import copy

import boto3
from cement import Controller, ex
from cement.utils.version import get_version_banner
from colored import attr

from ..core.version import get_version
from ..helpers import (
    SHIP, BLUE, RESET, HYDRA
)

VERSION_BANNER = f"""
Hydra manages many heads of networks {get_version()}
{get_version_banner()}
"""


class Base(Controller):  # pylint: disable=too-many-ancestors
    class Meta:
        label = 'base'

        # text displayed at the top of --help output
        description = 'Hydra manages many heads of networks'

        # text displayed at the bottom of --help output
        epilog = HYDRA

        # controller level arguments. ex: 'hydra --version'
        arguments = [
            # add a version banner
            (
                ['-v', '--version'],
                {
                    'action': 'version',
                    'version': VERSION_BANNER
                }
            ),
        ]

    def _default(self):
        """Default action if no sub-command is passed."""
        self.app.args.print_help()

    @property
    def utils(self):
        return self.app.utils

    @property
    def release(self):
        return self.app.release

    @ex(
        help='Print hydra configuration info',
    )
    def info(self):
        outputs = OrderedDict()

        outputs['Work Directory'] = self.utils.path()
        outputs['Project Name'] = self.app.config.get('hydra', 'project')

        # Build Binary
        outputs['Build Binary Path'] = self.release.dist_binary_path
        try:
            outputs['Build Binary Version'] = self.release.get_build_version()
        except IOError:
            outputs['Build Binary Version'] = '(doesnt exist)'

        # Dist Binary
        outputs['Dist Binary Path'] = self.release.dist_binary_path
        try:
            outputs['Dist Binary Version'] = self.release.get_dist_version()
        except IOError:
            outputs['Dist Binary Version'] = '(doesnt exist)'

        # AWS
        outputs['Release AWS Profile'] = self.app.config.get('release', 'aws_profile')
        outputs['S3 Dist Bucket'] = self.app.release.dist_bucket
        outputs['Boto Version'] = boto3.__version__

        outputs['Provision AWS Profile'] = self.app.config.get('provision', 'aws_profile')

        print(SHIP, RESET)
        for key, value in outputs.items():
            print(BLUE, f'{key:>20}\t:', attr('reset'), value)

    @ex(
        help='Make a new version of sidechain for release',
    )
    def make_dist(self):
        """Example sub-command."""
        build = self.release.get_build_version()

        self.app.log.info(f'Preparing release for distribution: {build}')

        self.app.log.debug(f'mkdir: {self.release.path()}')
        os.makedirs(self.release.path(), exist_ok=True)

        self.app.log.debug(f'copy: {self.release.build_binary_path} to {self.release.dist_binary_path}')
        copy(self.release.build_binary_path, self.release.dist_binary_path)

        manifest = {
            'version': build,
            'released': datetime.utcnow().strftime('%c'),
            'files': ['./shipchain', './manifest.json']
        }

        self.app.log.debug('writing manifest.json')
        manifest_file = self.release.path('manifest.json')
        json.dump(manifest, open(manifest_file, 'w+'), indent=2)

        self.app.log.info('Done making release!')

    @ex(
        help='Upload the latest release to S3',
    )
    def upload_dist(self):
        bucket = self.release.dist_bucket
        dist_version = self.release.get_dist_version()

        session = self.release.get_boto()
        s3 = session.resource('s3')
        self.app.log.info(f'Uploading distribution to S3: {bucket} @ {self.app.config.get("release", "aws_profile")}')

        self.app.log.debug(f'Making bucket: {bucket}')

        try:
            s3.Bucket(bucket)
            self.app.log.debug(f'Bucket created: {bucket}')
        except s3.meta.client.exceptions.BucketAlreadyOwnedByYou:
            self.app.log.debug(f'Already exists: {bucket}')

        dist = self.release.path() + '/'

        for dist_file in glob.glob(dist + '*'):
            local_fn = dist_file.replace(dist, '')
            for version in [f'archive/{dist_version}', 'latest']:
                s3_key = f'{version}/{local_fn}'
                self.app.log.debug(f'Uploading: dist/{local_fn} to {s3_key}')
                s3.Bucket(bucket).upload_file(Filename=dist_file, Key=s3_key, ExtraArgs={'ACL': 'public-read'})

        self.app.log.info('Done!')
        self.app.log.info('Release is available at:')
        self.app.log.info(f'https://{bucket}.s3.amazonaws.com/latest/manifest.json')

    @ex(
        help='Make a release and upload it',
    )
    def dist(self):
        self.make_dist()
        self.upload_dist()
