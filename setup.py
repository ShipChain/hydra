
from setuptools import setup, find_packages
from hydra.core.version import get_version

VERSION = get_version()

f = open('README.md', 'r')
LONG_DESCRIPTION = f.read()
f.close()

install_requires = []
dependency_links = []
with open('requirements.txt') as f:
    for line in f.read().splitlines():
        if line.startswith('-e git://'):
            # Handle non-pypi dependencies
            package = line.split('#egg=')[1]
            install_requires.append(package)
            dependency_links.append(f'{line.split("-e ")[1]}')
        else:
            install_requires.append(line)

setup(
    name='hydra',
    version=VERSION,
    description='Hydra manages many heads of networks',
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/markdown',
    author='Lee Bailey',
    author_email='lbailey@shipchain.io',
    url='https://github.com/shipchain/hydra',
    license='Apache-2.0',
    classifiers=[
        # Trove classifiers
        # Full list: https://pypi.python.org/pypi?%3Aaction=list_classifiers
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'License :: OSI Approved :: Apache Software License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
    ],
    keywords=[
        'blockchain',
        'shipchain',
        'loomnetwork',
        'loom',
        'hydra',
    ],
    packages=find_packages(exclude=['ez_setup', 'tests*']),
    package_data={'hydra': ['templates/*']},
    include_package_data=True,
    install_requires=install_requires,
    dependency_links=dependency_links,
    entry_points="""
        [console_scripts]
        hydra = hydra.main:main
    """,
)
