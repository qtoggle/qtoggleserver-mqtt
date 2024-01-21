from setuptools import setup, find_namespace_packages


setup(
    name='qtoggleserver-mqtt',
    version='unknown-version',
    description='Publish qToggleServer events to an MQTT broker',
    author='Calin Crisan',
    author_email='ccrisan@gmail.com',
    license='Apache 2.0',

    packages=find_namespace_packages(),

    install_requires=[
        'aiomqtt>=2.0',
    ]
)
