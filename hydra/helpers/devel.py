import os
from . import HydraHelper
class DevelHelper(HydraHelper):
    def path(self, extrapath=''):
        return os.path.realpath(
            os.path.join(
                self.app.utils.path(self.config['devel']['path']),
                extrapath)
        )

    def exec(self, *args):
        return self.app.utils.binary_exec(self.path('./shipchain'), *args)

    def get_dist_version(self):
        return self.app.utils.get_binary_version(self.app.utils.dist_binary_path)
