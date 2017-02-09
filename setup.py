from setuptools import setup

setup(
    name='gstreamer-player',
    version='1.0.0',
    description='Python 3 wrapper for playing media via gstreamer',
    url='https://github.com/happyleavesaoc/gstreamer-player/',
    license='MIT',
    author='happyleaves',
    author_email='happyleaves.tfr@gmail.com',
    packages=['gsp'],
    install_requires=['mutagen>=1.36.2'],
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
    ]
)
