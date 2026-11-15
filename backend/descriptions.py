"""Buyer-friendly copy from visible/reviewed fields; no generated value or condition claims."""
from html import escape
import re


def build_description(card):
    player = str(card.get('player') or '').strip()
    set_name = str(card.get('set_name') or '').strip()
    parallel = str(card.get('parallel') or '').strip()
    team = str(card.get('team') or '').strip()
    number = str(card.get('card_number') or '').strip().lstrip('#')
    year = card.get('year')
    print_run = card.get('print_run')
    headline = player or 'Trading card'
    highlights = []
    if parallel:
        highlights.append(parallel if re.search(r'\bparallel\b', parallel, re.I) else parallel + ' Parallel')
    if print_run and f'/{print_run}' not in parallel:
        highlights.append(f'/{print_run}')
    if highlights:
        headline += ' — ' + ' '.join(highlights)
    collection = set_name
    if year and not re.search(rf'\b{re.escape(str(year))}\b', set_name):
        collection = f'{year} {set_name}'.strip()
    if collection and player:
        intro = f'{collection} featuring {player}'
        if team:
            intro += f' of {team}'
        intro += '.'
    elif collection:
        intro = f'A trading card from {collection}.'
    else:
        intro = f'{player} trading card.' if player else 'Please see the photos for card details.'
    details = []
    if parallel:
        details.append(f'Parallel: {parallel}')
    if print_run:
        details.append(f'Print run: /{print_run}')
    if number:
        details.append(f'Card number: #{number}')
    for key, label in (('is_rookie', 'Rookie card (RC)'), ('is_auto', 'Autograph card'), ('is_relic', 'Relic card')):
        if card.get(key):
            details.append(label)
    blocks = [headline, intro]
    if details:
        blocks.append('\n'.join('- ' + detail for detail in details))
    blocks += ['Please review the photos for the card’s condition and details.',
               'Questions about the card? Please send a message.']
    return '\n\n'.join(blocks)


def description_html(text):
    """Keep paragraphs/bullets in the eBay CSV, escaping all entered text."""
    blocks = re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip())
    rendered = []
    for index, block in enumerate(blocks):
        lines = block.splitlines()
        if lines and all(line.startswith('- ') for line in lines):
            rendered.append('<ul>' + ''.join('<li>' + escape(line[2:]) + '</li>' for line in lines) + '</ul>')
        else:
            content = '<br>'.join(escape(line) for line in lines)
            rendered.append('<p>' + ('<strong>' + content + '</strong>' if index == 0 else content) + '</p>')
    return ''.join(rendered)
