from collections import Counter
from dataclasses import dataclass, field

from xdsl.dialects import riscv, riscv_func
from xdsl.dialects.builtin import ModuleOp
from xdsl.ir.core import Block, MLContext, Operation, Region
from xdsl.passes import ModulePass
from xdsl.pattern_rewriter import (
    PatternRewriter,
    PatternRewriteWalker,
    RewritePattern,
    op_type_rewrite_pattern,
)


class AddSections(RewritePattern):
    @op_type_rewrite_pattern
    def match_and_rewrite(self, op: ModuleOp, rewriter: PatternRewriter):
        # bss stands for block starting symbol
        heap_section = riscv.AssemblySectionOp(
            ".bss",
            Region(
                Block(
                    [
                        riscv.LabelOp("heap"),
                        riscv.DirectiveOp(".space", f"{1024}"),  # 1kb
                    ]
                )
            ),
        )
        data_section = riscv.AssemblySectionOp(".data", Region(Block()))
        text_section = riscv.AssemblySectionOp(
            ".text", rewriter.move_region_contents_to_new_regions(op.regions[0])
        )

        op.body.add_block(Block([heap_section, data_section, text_section]))


@dataclass
class DataDirectiveRewritePattern(RewritePattern):
    _data_section: riscv.AssemblySectionOp | None = None
    _counter: Counter[str] = field(default_factory=Counter)

    def data_section(self, op: Operation) -> riscv.AssemblySectionOp:
        """
        Relies on the data directive being inserted earlier
        """
        if self._data_section is None:
            module_op = op.get_toplevel_object()
            assert isinstance(
                module_op, ModuleOp
            ), f"The top level object of {str(op)} must be a ModuleOp"

            for op in module_op.body.blocks[0].ops:
                if not isinstance(op, riscv.AssemblySectionOp):
                    continue
                if op.directive.data != ".data":
                    continue
                self._data_section = op

            assert self._data_section is not None

        return self._data_section

    def label(self, func_name: str) -> str:
        key = func_name
        count = self._counter[key]
        self._counter[key] += 1
        return f"{key}.{count}"

    def add_data(self, op: Operation, label: str, data: list[int]):
        encoded_data = ", ".join(hex(el) for el in data)
        self.data_section(op).regions[0].blocks[0].add_ops(
            [riscv.LabelOp(label), riscv.DirectiveOp(".word", encoded_data)]
        )


class SetupRiscvPass(ModulePass):
    name = "setup-lowering-to-riscv"

    def apply(self, ctx: MLContext, op: ModuleOp) -> None:
        PatternRewriteWalker(AddSections()).rewrite_module(op)


class ChangeBlockArgumentTypes(RewritePattern):
    @op_type_rewrite_pattern
    def match_and_rewrite(self, op: riscv_func.FuncOp, rewriter: PatternRewriter):
        """
        Cast all branch arguments to riscv registers
        """
        for block in op.func_body.blocks:
            for arg in block.args:
                if not isinstance(arg.type, riscv.IntRegisterType):
                    rewriter.modify_block_argument_type(
                        arg, riscv.IntRegisterType.unallocated()
                    )


class FinalizeRiscvPass(ModulePass):
    name = "finalize-lowering-to-riscv"

    def apply(self, ctx: MLContext, op: ModuleOp) -> None:
        PatternRewriteWalker(ChangeBlockArgumentTypes()).rewrite_module(op)
