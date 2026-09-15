"""Small evaluator for the actual guard expressions under test, not workflow execution.

Supports only the boolean/context/function subset used by this repository.
Models Actions truthiness, case-insensitive strings and loose equality so tests
cannot accidentally treat a malformed string input as a typed boolean.
"""
import json
import re


def truth(value):
    return value is not None and value is not False and value != 0 and value != ""


def equal(left, right):
    if type(left) is type(right):
        return left.lower() == right.lower() if isinstance(left, str) else left == right
    def number(value):
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                result = json.loads(value)
                return result if type(result) in (int, float) else float("nan")
            except json.JSONDecodeError:
                return float("nan")
        return float("nan")
    return number(left) == number(right)


class Expression:
    def __init__(self, text):
        text = text.strip().removeprefix("${{").removesuffix("}}").strip()
        self.tokens = []
        while text:
            match = re.match(r"\s*('(?:[^']|'')*'|[A-Za-z_][\w.]*|&&|\|\||==|!=|[!(),])", text)
            if not match:
                raise ValueError("Unsupported Actions guard syntax: " + text)
            self.tokens.append(match[1])
            text = text[match.end():]
        self.index = 0
        self.tree = self.parse_or()
        if self.index != len(self.tokens):
            raise ValueError("Unexpected guard tokens")

    def take(self, value):
        if self.index < len(self.tokens) and self.tokens[self.index] == value:
            self.index += 1
            return True
        return False

    def parse_or(self):
        node = self.parse_and()
        while self.take("||"):
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_comparison()
        while self.take("&&"):
            node = ("and", node, self.parse_comparison())
        return node

    def parse_comparison(self):
        node = self.parse_value()
        for operator in ("==", "!="):
            if self.take(operator):
                return (operator, node, self.parse_value())
        return node

    def parse_value(self):
        if self.take("!"):
            return ("not", self.parse_value())
        if self.take("("):
            node = self.parse_or()
            if not self.take(")"):
                raise ValueError("Unclosed guard expression")
            return node
        token = self.tokens[self.index]
        self.index += 1
        if token.startswith("'"):
            return ("literal", token[1:-1].replace("''", "'"))
        if token in ("true", "false", "null"):
            return ("literal", json.loads(token))
        if self.take("("):
            args = [self.parse_or()]
            while self.take(","):
                args.append(self.parse_or())
            if not self.take(")") or token not in ("toJSON", "startsWith"):
                raise ValueError("Unsupported guard function")
            return (token, *args)
        return ("context", token)

    def evaluate(self, context):
        def visit(node):
            operator, *args = node
            if operator == "literal":
                return args[0]
            if operator == "context":
                return context.get(args[0])
            values = [visit(arg) for arg in args]
            if operator == "and":
                return truth(values[0]) and truth(values[1])
            if operator == "or":
                return truth(values[0]) or truth(values[1])
            if operator == "not":
                return not truth(values[0])
            if operator == "==":
                return equal(*values)
            if operator == "!=":
                return not equal(*values)
            if operator == "toJSON":
                return json.dumps(values[0], separators=(",", ":"))
            if operator == "startsWith":
                return str(values[0]).lower().startswith(str(values[1]).lower())
            raise ValueError("Unsupported guard node")
        return truth(visit(self.tree))
