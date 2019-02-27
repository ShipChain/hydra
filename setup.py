
from setuptools import setup, find_packages
from hydra.core.version import get_version

VERSION = get_version()

f = open('README.md', 'r')
LONG_DESCRIPTION = f.read()
f.close()

setup(
    name='hydra',
    version=VERSION,
    description='Hydra manages many heads of networks',
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/markdown',
    author='Lee Bailey',
    author_email='lbailey@shipchain.io',
    url='https://github.com/shipchain/hydra',
    license='unlicensed',
    packages=find_packages(exclude=['ez_setup', 'tests*']),
    package_data={'hydra': ['templates/*']},
    include_package_data=True,
    entry_points="""
        [console_scripts]
        hydra = hydra.main:main
    """,
)
