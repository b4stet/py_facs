from abc import ABCMeta, abstractmethod
import click
import json
import yaml
import csv
import sys


class AbstractCommand():
    OUTPUT_JSON = 'json'
    OUTPUT_CSV = 'csv'
    OUTPUT_FORMATS = [OUTPUT_JSON, OUTPUT_CSV]

    def __init__(self, data):
        file_path = './data/' + data
        with open(file_path, mode='r', encoding='utf-8') as f:
            self._data = yaml.safe_load(f)

    @abstractmethod
    def get_commands(self):
        raise NotImplementedError('Method get_commands must be implemented on class {}'.format(type(self)))

    def _print_formatted(self, format: str, data: list):
        if len(data) == 0:
            return

        if format == self.OUTPUT_JSON:
            print(json.dumps(data))

        if format == self.OUTPUT_CSV:
            writer = csv.DictWriter(sys.stdout, quoting=csv.QUOTE_MINIMAL, quotechar='"', fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)

    def _print_text(self, title: str, data: list):
        print('[+]', title)
        for elt in data:
            print(' |', elt)
        print('')

    def _get_option_output(self):
        return click.Option(
            ['--output', '-o', 'output'],
            help='Output format for the result. Default is json',
            default='json',
            type=click.Choice(self.OUTPUT_FORMATS)
        )

    def _get_option_input(self):
        return click.Option(
            ['--input', '-i', 'input'],
            help='Path to the disk dump.',
            type=str
        )
