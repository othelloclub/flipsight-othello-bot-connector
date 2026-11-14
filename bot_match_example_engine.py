#!/usr/bin/env python3
"""Protocol-only example: choose the lowest-index legal move, not a strong bot."""
import json
import sys


def legal_moves(board, color):
    result = []
    for square, cell in enumerate(board):
        if cell:
            continue
        row, col = divmod(square, 8)
        for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            r, c, captured = row + dr, col + dc, 0
            while 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == 3 - color:
                captured += 1
                r, c = r + dr, c + dc
            if captured and 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == color:
                result.append(square)
                break
    return result


def choose(request):
    board, color = request.get('board'), request.get('color')
    n=request.get('board_size',8)
    if n not in (8,10) or not isinstance(board, list) or len(board) != n*n or any(type(v) is not int or v not in (0, 1, 2) for v in board):
        raise ValueError('board must match board_size and contain integers in 0..2')
    if type(color) is not int or color not in (1, 2):
        raise ValueError('color must be 1 or 2')
    # The connector sends legal_moves (2026-11-12); computing them here keeps older connectors working.
    moves = request.get('legal_moves')
    if not isinstance(moves, list) or any(type(v) is not int or not 0 <= v < n*n for v in moves):
        if request.get('game_mode','std')!='std':raise ValueError('variant requires server legal moves')
        moves = legal_moves(board, color)
    return moves[0] if moves else -1


def main():
    # The connector keeps this process for the whole game: read one request line, answer one line, repeat.
    # Do not read until end of input (sys.stdin.read() would wait forever). Flush after every answer.
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            move = choose(json.loads(line[:16385]))
        except (ValueError, TypeError, AttributeError):
            print('Invalid engine request', file=sys.stderr, flush=True)
            move = -1
        print(json.dumps({'move': move}), flush=True)


if __name__ == '__main__':
    main()
