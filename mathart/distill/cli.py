"""CLI for knowledge distillation pipeline."""
from __future__ import annotations
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mathart-distill",
        description="Knowledge distillation pipeline: parse, compile, optimize.",
    )
    sub = parser.add_subparsers(dest="command")

    # Parse command
    p_parse = sub.add_parser("parse", help="Parse knowledge files into structured rules")
    p_parse.add_argument(
        "--input", default="mathart/knowledge",
        help="Directory containing knowledge .md files",
    )
    p_parse.add_argument(
        "--output", default="distilled_rules.json",
        help="Output JSON file for parsed rules",
    )

    # Compile command
    p_compile = sub.add_parser("compile", help="Compile rules into parameter spaces")
    p_compile.add_argument(
        "--rules", default="distilled_rules.json",
        help="Input JSON file with parsed rules",
    )
    p_compile.add_argument(
        "--output", default="param_spaces.json",
        help="Output JSON file for parameter spaces",
    )

    # Summary command
    p_summary = sub.add_parser("summary", help="Show summary of parsed rules")
    p_summary.add_argument(
        "--rules", default="distilled_rules.json",
        help="Input JSON file with parsed rules",
    )

    # Optimize command
    p_opt = sub.add_parser("optimize", help="Run evolutionary optimization")
    p_opt.add_argument(
        "--space", default="param_spaces.json",
        help="Input JSON file with parameter spaces",
    )
    p_opt.add_argument(
        "--module", default=None,
        help="Target module to optimize (e.g., 'animation')",
    )
    p_opt.add_argument(
        "--generations", type=int, default=50,
        help="Number of generations",
    )
    p_opt.add_argument(
        "--population", type=int, default=30,
        help="Population size",
    )
    p_opt.add_argument("--seed", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "parse":
        _cmd_parse(args)
    elif args.command == "compile":
        _cmd_compile(args)
    elif args.command == "summary":
        _cmd_summary(args)
    elif args.command == "optimize":
        _cmd_optimize(args)
    else:
        parser.print_help()


def _cmd_parse(args):
    from .parser import KnowledgeParser

    parser = KnowledgeParser()
    input_path = Path(args.input)

    if input_path.is_dir():
        rules = parser.parse_directory(input_path)
    else:
        rules = parser.parse_markdown(input_path)

    parser.save_rules(rules, args.output)
    summary = parser.rules_summary(rules)
    print(f"Parsed {summary['total_rules']} rules from {args.input}")
    print(f"  By module: {summary['by_module']}")
    print(f"  By type:   {summary['by_type']}")
    print(f"  Saved to:  {args.output}")


def _cmd_compile(args):
    import json
    from .parser import KnowledgeParser
    from .compiler import RuleCompiler

    rules = KnowledgeParser.load_rules(args.rules)
    compiler = RuleCompiler()
    spaces = compiler.compile_by_module(rules)

    # Save all spaces to one JSON
    output = {name: space.to_dict() for name, space in spaces.items()}
    Path(args.output).write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Compiled {len(rules)} rules into {len(spaces)} parameter spaces:")
    for name, space in spaces.items():
        print(f"  {name}: {space.dimensions} dimensions")
    print(f"  Saved to: {args.output}")


def _cmd_summary(args):
    from .parser import KnowledgeParser

    rules = KnowledgeParser.load_rules(args.rules)
    summary = KnowledgeParser.rules_summary(rules)

    print(f"Total rules: {summary['total_rules']}")
    print(f"\nBy module:")
    for mod, count in sorted(summary["by_module"].items()):
        print(f"  {mod}: {count}")
    print(f"\nBy type:")
    for rt, count in sorted(summary["by_type"].items()):
        print(f"  {rt}: {count}")

    print(f"\nRule details:")
    for rule in rules[:10]:
        print(f"  [{rule.id}] {rule.description[:60]}...")


def _cmd_optimize(args):
    import json
    from .compiler import ParameterSpace
    from .optimizer import EvolutionaryOptimizer, constraint_satisfaction_fitness

    data = json.loads(Path(args.space).read_text(encoding="utf-8"))

    if args.module and args.module in data:
        space = ParameterSpace.from_dict(data[args.module])
    else:
        # Merge all spaces
        space = ParameterSpace(name="merged")
        for name, space_data in data.items():
            s = ParameterSpace.from_dict(space_data)
            for k, c in s.constraints.items():
                space.add_constraint(c)

    if space.dimensions == 0:
        print("No optimizable parameters found.")
        return

    fitness_fn = constraint_satisfaction_fitness(space)
    optimizer = EvolutionaryOptimizer(
        space,
        population_size=args.population,
        seed=args.seed,
    )

    def on_gen(gen, best):
        if gen % 10 == 0:
            print(f"  Gen {gen:3d}: best_fitness={best.fitness:.4f}")

    print(f"Optimizing {space.dimensions} parameters over {args.generations} generations...")
    best = optimizer.run(fitness_fn, generations=args.generations, callback=on_gen)

    print(f"\nBest fitness: {best.fitness:.4f}")
    print(f"Best params:")
    for k, v in sorted(best.params.items()):
        print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
