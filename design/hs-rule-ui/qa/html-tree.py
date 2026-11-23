"""Parse project HTML for the optional DOM-model regression checks; no backend code."""
from html.parser import HTMLParser
from pathlib import Path
import json
import sys

class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {'tag': 'document', 'attrs': {}, 'children': []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = {'tag': tag, 'attrs': dict(attrs), 'children': []}
        self.stack[-1]['children'].append(element)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        if self.stack[-1]['tag'] == tag:
            self.stack.pop()

    def handle_data(self, value):
        self.stack[-1]['children'].append(value)

project = Path(sys.argv[1])
result = {}
for filename in ['index.html', 'login.html', 'upload.html']:
    parser = Tree()
    parser.feed((project / filename).read_text())
    result[filename] = parser.root
print(json.dumps(result))
