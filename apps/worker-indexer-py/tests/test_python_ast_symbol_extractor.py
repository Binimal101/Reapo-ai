from ast_indexer.parsing.python_ast_symbol_extractor import PythonAstSymbolExtractor


def test_extracts_functions_classes_methods_and_callees() -> None:
    source = '''
class Checkout:
    def validate(self, cart):
        apply_discount(cart)

async def fetch_user(user_id):
    return user_id

def process(order_id):
    user = fetch_user(order_id)
    return user
'''

    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='checkout-service', path='src/orders.py', source=source)

    names = [symbol.symbol for symbol in extracted.symbols]
    assert 'Checkout' in names
    assert 'Checkout.validate' in names
    assert 'fetch_user' in names
    assert 'process' in names

    process_symbol = next(s for s in extracted.symbols if s.symbol == 'process')
    assert process_symbol.callees == ('fetch_user',)


def test_resolves_import_aliases_and_self_method_calls() -> None:
    source = '''
from discounts.engine import apply_discount as discount
import pricing.tools as tools

class Checkout:
    def validate(self, total):
        return discount(total)

    def process(self, total):
        adjusted = self.validate(total)
        return tools.compute(adjusted)
'''

    extractor = PythonAstSymbolExtractor()
    extracted = extractor.extract(repo='checkout-service', path='src/checkout.py', source=source)

    validate = next(s for s in extracted.symbols if s.symbol == 'Checkout.validate')
    process = next(s for s in extracted.symbols if s.symbol == 'Checkout.process')

    assert validate.callees == ('discounts.engine.apply_discount',)
    assert process.callees == ('Checkout.validate', 'pricing.tools.compute')
