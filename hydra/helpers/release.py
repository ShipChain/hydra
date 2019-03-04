from . import HydraHelper
import boto3
import os
class ReleaseHelper(HydraHelper):
    def path(self, extrapath=''):
        return os.path.realpath(os.path.join(
            self.app.utils.path(self.config['release']['distdir']),
            extrapath
        ))

    @property
    def build_binary_path(self):
        return self.app.utils.path(self.config.get('release', 'build_binary_path'))

    @property
    def dist_binary_path(self):
        return os.path.join(
            self.app.utils.path(self.config.get('release', 'distdir')),
            self.app.utils.binary_name)
    
    @property
    def dist_bucket(self):
        return self.config.get('release', 'aws_s3_dist_bucket') % self.config['hydra']

    def dist_exec(self, *args):
        return self.app.utils.binary_exec(self.dist_binary_path, *args)

    def get_dist_version(self):
        return self.app.utils.get_binary_version(self.dist_binary_path)

    def get_build_version(self):
        return self.app.utils.get_binary_version(self.build_binary_path)
    
    def get_boto(self):
        return boto3.Session(profile_name=self.config.get('release', 'aws_profile'))

