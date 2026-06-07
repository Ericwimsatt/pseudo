"""
transformations.py

Table of Python → pseudocode transformations used by file_to_pseudo.py.

Each entry is a dict with:
  name        : short identifier for the transformation
  node_type   : tree-sitter node type that triggers it
  pattern     : schematic Python pattern  (use N, x, expr, var as placeholders)
  pseudocode  : schematic pseudocode output
  example_in  : concrete Python input taken from run_simulation.py
  example_out : concrete pseudocode output

These are applied by file_to_pseudo.py when walking the AST.
"""

TRANSFORMATIONS = [
    # ------------------------------------------------------------------
    # For loops
    # ------------------------------------------------------------------
    {
        "name": "for_blank_range",
        "node_type": "for_statement",
        "pattern": "for _ in range(N):",
        "pseudocode": "do N times:",
        "example_in": "for _ in range(hands):",
        "example_out": "do hands times:",
    },
    {
        "name": "for_var_range",
        "node_type": "for_statement",
        "pattern": "for x in range(N):",
        "pseudocode": "for x from 0 to N - 1:",
        "example_in": "for i in range(10):",
        "example_out": "for i from 0 to 10 - 1:",
    },
    {
        "name": "for_each",
        "node_type": "for_statement",
        "pattern": "for x in iterable:",
        "pseudocode": "for each x in iterable:",
        "example_in": "for key in play_stats:",
        "example_out": "for each key in play_stats:",
    },
    # ------------------------------------------------------------------
    # Function definitions
    # ------------------------------------------------------------------
    {
        "name": "function_def",
        "node_type": "function_definition",
        "pattern": "def name(params):",
        "pseudocode": "function name(params):",
        "example_in": "def play_game(pre_flop_strategy, post_flop_strategy, river_strategy, dead_card_maker):",
        "example_out": "function play_game(pre_flop_strategy, post_flop_strategy, river_strategy, dead_card_maker):",
    },
    # ------------------------------------------------------------------
    # If / elif / else
    # ------------------------------------------------------------------
    {
        "name": "if_statement",
        "node_type": "if_statement",
        "pattern": "if condition:",
        "pseudocode": "if condition:",
        "example_in": "if player_rank <= 1:",
        "example_out": "if player_rank <= 1:",
    },
    {
        "name": "elif_clause",
        "node_type": "elif_clause",
        "pattern": "elif condition:",
        "pseudocode": "else if condition:",
        "example_in": "elif player_rank <= 10:",
        "example_out": "else if player_rank <= 10:",
    },
    {
        "name": "else_clause",
        "node_type": "else_clause",
        "pattern": "else:",
        "pseudocode": "otherwise:",
        "example_in": "else:",
        "example_out": "otherwise:",
    },
    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------
    {
        "name": "assignment",
        "node_type": "assignment",
        "pattern": "var = value",
        "pseudocode": "set var to value",
        "example_in": "play_bet = 0",
        "example_out": "set play_bet to 0",
    },
    {
        "name": "augmented_add",
        "node_type": "augmented_assignment",
        "pattern": "var += expr",
        "pseudocode": "add expr to var",
        "example_in": "total += result",
        "example_out": "add result to total",
    },
    {
        "name": "augmented_sub",
        "node_type": "augmented_assignment",
        "pattern": "var -= expr",
        "pseudocode": "subtract expr from var",
        "example_in": "score -= 1",
        "example_out": "subtract 1 from score",
    },
    {
        "name": "augmented_mul",
        "node_type": "augmented_assignment",
        "pattern": "var *= expr",
        "pseudocode": "multiply var by expr",
        "example_in": "total *= 2",
        "example_out": "multiply total by 2",
    },
    {
        "name": "augmented_div",
        "node_type": "augmented_assignment",
        "pattern": "var /= expr",
        "pseudocode": "divide var by expr",
        "example_in": "result /= n",
        "example_out": "divide result by n",
    },
    {
        "name": "augmented_floordiv",
        "node_type": "augmented_assignment",
        "pattern": "var //= expr",
        "pseudocode": "floor-divide var by expr",
        "example_in": "total //= 2",
        "example_out": "floor-divide total by 2",
    },
    {
        "name": "augmented_mod",
        "node_type": "augmented_assignment",
        "pattern": "var %= expr",
        "pseudocode": "var mod= expr",
        "example_in": "idx %= length",
        "example_out": "idx mod= length",
    },
    {
        "name": "augmented_pow",
        "node_type": "augmented_assignment",
        "pattern": "var **= expr",
        "pseudocode": "raise var to the power of expr",
        "example_in": "val **= 2",
        "example_out": "raise val to the power of 2",
    },
    {
        "name": "list_extend_assign",
        "node_type": "augmented_assignment",
        "pattern": "list += iterable",
        "pseudocode": "add iterable to list",
        "example_in": "board += deck.draw(3)",
        "example_out": "add deck.draw(3) to board",
    },
    # ------------------------------------------------------------------
    # Return
    # ------------------------------------------------------------------
    {
        "name": "return_value",
        "node_type": "return_statement",
        "pattern": "return value",
        "pseudocode": "return value",
        "example_in": "return 500",
        "example_out": "return 500",
    },
    {
        "name": "return_void",
        "node_type": "return_statement",
        "pattern": "return",
        "pseudocode": "return",
        "example_in": "return",
        "example_out": "return",
    },
    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------
    {
        "name": "comment",
        "node_type": "comment",
        "pattern": "# comment text",
        "pseudocode": "// comment text",
        "example_in": "# Check if dealer qualifies",
        "example_out": "// Check if dealer qualifies",
    },
    # ------------------------------------------------------------------
    # Imports  (skipped — implementation detail, not pseudocode logic)
    # ------------------------------------------------------------------
    {
        "name": "import_skip",
        "node_type": "import_statement",
        "pattern": "import module",
        "pseudocode": "(skipped)",
        "example_in": "import sys",
        "example_out": "(skipped)",
    },
    {
        "name": "import_from_skip",
        "node_type": "import_from_statement",
        "pattern": "from module import x",
        "pseudocode": "(skipped)",
        "example_in": "from treys import Card, Deck, evaluation",
        "example_out": "(skipped)",
    },
    # ------------------------------------------------------------------
    # Expression statements  (calls kept as raw text for now)
    # ------------------------------------------------------------------
    {
        "name": "function_call",
        "node_type": "expression_statement > call",
        "pattern": "func(args)",
        "pseudocode": "func(args)  [raw text]",
        "example_in": "print('Average result: {}'.format(total / hands))",
        "example_out": "print('Average result: {}'.format(total / hands))",
    },
    # ------------------------------------------------------------------
    # Docstrings  (skipped — not executable logic)
    # ------------------------------------------------------------------
    {
        "name": "docstring_skip",
        "node_type": "expression_statement > string",
        "pattern": '"""docstring"""',
        "pseudocode": "(skipped)",
        "example_in": '"""Return the first string ..."""',
        "example_out": "(skipped)",
    },
]
