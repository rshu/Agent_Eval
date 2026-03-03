#!/bin/bash
set -e

# Change to project root so generated_patches/ output lands under the root directory
cd "$(dirname "$0")/.."

TOTAL=36
CURRENT=0

run_task() {
    CURRENT=$((CURRENT + 1))
    local project="$1"
    local variant="$2"
    shift 2
    echo "============================================"
    echo "[$CURRENT/$TOTAL] Running $project - $variant"
    echo "============================================"
    "$@"
    echo "[$CURRENT/$TOTAL] Completed $project - $variant"
    echo ""
}

echo "Starting evaluation run: $TOTAL tasks"
echo ""

## Hutool ##
echo ">>> Project: Hutool (9 tasks) <<<"
run_task "Hutool" "pr_630_v1" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_630_v1.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/630.patch --branch pr_630
run_task "Hutool" "pr_630_v2" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_630_v2.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/630.patch --branch pr_630
run_task "Hutool" "pr_630_v3" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_630_v3.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/630.patch --branch pr_630

run_task "Hutool" "pr_692_v1" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_692_v1.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/692.patch --branch pr_692
run_task "Hutool" "pr_692_v2" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_692_v2.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/692.patch --branch pr_692
run_task "Hutool" "pr_692_v3" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_692_v3.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/692.patch --branch pr_692

run_task "Hutool" "pr_1263_v1" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_1263_v1.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/1263.patch --branch pr_1263
run_task "Hutool" "pr_1263_v2" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_1263_v2.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/1263.patch --branch pr_1263
run_task "Hutool" "pr_1263_v3" agent-eval --mode run -d /home/rshu/hutool -f ./prompt_variants/Hutool/pr_1263_v3.md --gt-patch https://gitee.com/chinabugotech/hutool/pulls/1263.patch --branch pr_1263


## MindSpore ##
echo ">>> Project: MindSpore (9 tasks) <<<"
run_task "MindSpore" "pr_90051_v1" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90051_v1.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90051.patch --branch pr_90051
run_task "MindSpore" "pr_90051_v2" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90051_v2.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90051.patch --branch pr_90051
run_task "MindSpore" "pr_90051_v3" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90051_v3.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90051.patch --branch pr_90051

run_task "MindSpore" "pr_90629_v1" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90629_v1.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90629.patch --branch pr_90629
run_task "MindSpore" "pr_90629_v2" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90629_v2.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90629.patch --branch pr_90629
run_task "MindSpore" "pr_90629_v3" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90629_v3.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90629.patch --branch pr_90629

run_task "MindSpore" "pr_90911_v1" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90911_v1.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90911.patch --branch pr_90911
run_task "MindSpore" "pr_90911_v2" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90911_v2.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90911.patch --branch pr_90911
run_task "MindSpore" "pr_90911_v3" agent-eval --mode run -d /home/rshu/mindspore -f ./prompt_variants/MindSpore/pr_90911_v3.md --gt-patch https://gitee.com/mindspore/mindspore/pulls/90911.patch --branch pr_90911


## Triton-Ascend ##
echo ">>> Project: Triton-Ascend (9 tasks) <<<"
run_task "Triton-Ascend" "pr_17_v1" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_17_v1.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/17.patch --branch pr_17
run_task "Triton-Ascend" "pr_17_v2" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_17_v2.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/17.patch --branch pr_17
run_task "Triton-Ascend" "pr_17_v3" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_17_v3.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/17.patch --branch pr_17

run_task "Triton-Ascend" "pr_71_v1" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_71_v1.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/71.patch --branch pr_71
run_task "Triton-Ascend" "pr_71_v2" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_71_v2.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/71.patch --branch pr_71
run_task "Triton-Ascend" "pr_71_v3" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_71_v3.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/71.patch --branch pr_71

run_task "Triton-Ascend" "pr_333_v1" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_333_v1.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/333.patch --branch pr_333
run_task "Triton-Ascend" "pr_333_v2" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_333_v2.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/333.patch --branch pr_333
run_task "Triton-Ascend" "pr_333_v3" agent-eval --mode run -d /home/rshu/triton-ascend -f ./prompt_variants/Triton-Ascend/pr_333_v3.md --gt-patch https://gitee.com/ascend/triton-ascend/pulls/333.patch --branch pr_333

## Pytorch ##
echo ">>> Project: Pytorch (9 tasks) <<<"
run_task "Pytorch" "pr_123811_v1" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_123811_v1.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/123811.patch --branch pr_123811
run_task "Pytorch" "pr_123811_v2" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_123811_v2.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/123811.patch --branch pr_123811
run_task "Pytorch" "pr_123811_v3" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_123811_v3.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/123811.patch --branch pr_123811

run_task "Pytorch" "pr_135433_v1" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_135433_v1.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/135433.patch --branch pr_135433
run_task "Pytorch" "pr_135433_v2" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_135433_v2.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/135433.patch --branch pr_135433
run_task "Pytorch" "pr_135433_v3" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_135433_v3.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/135433.patch --branch pr_135433

run_task "Pytorch" "pr_163081_v1" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_163081_v1.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/163081.patch --branch pr_163081
run_task "Pytorch" "pr_163081_v2" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_163081_v2.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/163081.patch --branch pr_163081
run_task "Pytorch" "pr_163081_v3" agent-eval --mode run -d /home/rshu/pytorch -f ./prompt_variants/Pytorch/pr_163081_v3.md --gt-patch https://patch-diff.githubusercontent.com/raw/pytorch/pytorch/pull/163081.patch --branch pr_163081

echo ""
echo "============================================"
echo "All $TOTAL tasks completed successfully!"
echo "============================================"
