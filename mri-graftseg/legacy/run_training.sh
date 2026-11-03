#!/usr/bin/env bash
# =============================================================================
# run_training.sh
# ===============
# GPU-aware nnU-Net training launcher for Dataset002_GraftSeg, 5-fold CV,
# 1500 epochs per fold.
#
# Usage:
#   bash run_training.sh
#
# To resume interrupted training for a specific fold:
#   bash run_training.sh --resume
#
# Requirements:
#   - nnUNet_raw, nnUNet_preprocessed, nnUNet_results must be set below
#   - Dataset002 must be preprocessed:
#       nnUNetv2_plan_and_preprocess -d 2 -c 3d_fullres --verify_dataset_integrity
#   - splits_final.json must be in $nnUNet_preprocessed/Dataset002_GraftSeg/
# =============================================================================

set -euo pipefail

# =============================================================================
# CONFIGURE THESE PATHS BEFORE RUNNING
# =============================================================================
export nnUNet_raw="/media/pasca/Extreme SSD/MRI NeuralNetwork/nnUNet_raw"
export nnUNet_preprocessed="/media/pasca/Extreme SSD/MRI NeuralNetwork/nnUNet_preprocessed"
export nnUNet_results="/media/pasca/Extreme SSD/MRI NeuralNetwork/nnUNet_results"

DATASET_ID=2
CONFIG="3d_fullres"
N_EPOCHS=1500
N_FOLDS=5
RESUME_FLAG=""

# =============================================================================
# Parse arguments
# =============================================================================
for arg in "$@"; do
    case $arg in
        --resume)
            RESUME_FLAG="--c"
            echo "Resume mode: will continue from checkpoint_latest.pth for each fold"
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: bash run_training.sh [--resume]"
            exit 1
            ;;
    esac
done

# =============================================================================
# Verify environment
# =============================================================================
echo "============================================================"
echo "nnU-Net Training Launcher"
echo "============================================================"
echo "Dataset:    $DATASET_ID ($CONFIG)"
echo "Epochs:     $N_EPOCHS"
echo "Folds:      0 to $((N_FOLDS - 1))"
echo ""
echo "Paths:"
echo "  nnUNet_raw:          $nnUNet_raw"
echo "  nnUNet_preprocessed: $nnUNet_preprocessed"
echo "  nnUNet_results:      $nnUNet_results"
echo ""

# Check preprocessed data exists
PREPROC_DIR="$nnUNet_preprocessed/Dataset00${DATASET_ID}_GraftSeg"
if [ ! -d "$PREPROC_DIR" ]; then
    echo "ERROR: Preprocessed data not found at: $PREPROC_DIR"
    echo "Run first: nnUNetv2_plan_and_preprocess -d $DATASET_ID -c $CONFIG --verify_dataset_integrity"
    exit 1
fi

# Check splits file exists
SPLITS_FILE="$PREPROC_DIR/splits_final.json"
if [ ! -f "$SPLITS_FILE" ]; then
    echo "ERROR: splits_final.json not found at: $SPLITS_FILE"
    echo "Run first: python generate_splits.py --imagesTr ... --out \"$SPLITS_FILE\""
    exit 1
fi

echo "Preprocessed data: OK"
echo "splits_final.json: OK"
echo ""

# =============================================================================
# Detect GPUs
# =============================================================================
if command -v nvidia-smi &> /dev/null; then
    NUM_GPUS=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)
    echo "Detected $NUM_GPUS GPU(s):"
    nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null | \
        awk -F',' '{printf "  GPU %s: %s (%s)\n", $1, $2, $3}'
else
    NUM_GPUS=0
    echo "WARNING: nvidia-smi not found. Assuming CPU-only (training will be very slow)."
fi
echo ""

# =============================================================================
# Training strategy based on GPU count
# =============================================================================
if [ "$NUM_GPUS" -ge "$N_FOLDS" ]; then
    # -------------------------------------------------------------------------
    # 5+ GPUs: run all folds in parallel, one GPU per fold
    # -------------------------------------------------------------------------
    echo "Strategy: PARALLEL — running all $N_FOLDS folds simultaneously (1 GPU per fold)"
    echo "============================================================"

    PIDS=()
    for fold in $(seq 0 $((N_FOLDS - 1))); do
        GPU_ID=$fold
        LOG_FILE="$nnUNet_results/Dataset00${DATASET_ID}_GraftSeg/training_fold${fold}.log"
        mkdir -p "$(dirname "$LOG_FILE")"

        echo "Launching fold $fold on GPU $GPU_ID → log: $LOG_FILE"
        CUDA_VISIBLE_DEVICES=$GPU_ID nnUNetv2_train \
            $DATASET_ID $CONFIG $fold \
            --npz \
            -num_epochs $N_EPOCHS \
            $RESUME_FLAG \
            > "$LOG_FILE" 2>&1 &
        PIDS+=($!)
    done

    echo ""
    echo "All folds launched. Waiting for completion..."
    echo "Monitor progress with: tail -f <log_file>"
    echo ""

    # Wait for all and report
    FAILED=0
    for i in "${!PIDS[@]}"; do
        fold=$i
        pid=${PIDS[$i]}
        if wait "$pid"; then
            echo "Fold $fold: COMPLETED"
        else
            echo "Fold $fold: FAILED (exit code $?)"
            FAILED=$((FAILED + 1))
        fi
    done

    if [ "$FAILED" -gt 0 ]; then
        echo ""
        echo "WARNING: $FAILED fold(s) failed. Check log files."
        exit 1
    fi

elif [ "$NUM_GPUS" -ge 2 ]; then
    # -------------------------------------------------------------------------
    # 2-4 GPUs: run as many folds in parallel as GPUs, queue the rest
    # -------------------------------------------------------------------------
    echo "Strategy: BATCHED PARALLEL — running $NUM_GPUS folds at a time"
    echo "============================================================"

    fold=0
    while [ "$fold" -lt "$N_FOLDS" ]; do
        PIDS=()
        BATCH_FOLDS=()

        # Launch up to NUM_GPUS folds
        for gpu in $(seq 0 $((NUM_GPUS - 1))); do
            if [ "$fold" -ge "$N_FOLDS" ]; then
                break
            fi

            LOG_FILE="$nnUNet_results/Dataset00${DATASET_ID}_GraftSeg/training_fold${fold}.log"
            mkdir -p "$(dirname "$LOG_FILE")"

            echo "Launching fold $fold on GPU $gpu → log: $LOG_FILE"
            CUDA_VISIBLE_DEVICES=$gpu nnUNetv2_train \
                $DATASET_ID $CONFIG $fold \
                --npz \
                -num_epochs $N_EPOCHS \
                $RESUME_FLAG \
                > "$LOG_FILE" 2>&1 &
            PIDS+=($!)
            BATCH_FOLDS+=($fold)
            fold=$((fold + 1))
        done

        echo "Waiting for batch: folds ${BATCH_FOLDS[*]}..."
        for i in "${!PIDS[@]}"; do
            f=${BATCH_FOLDS[$i]}
            pid=${PIDS[$i]}
            if wait "$pid"; then
                echo "  Fold $f: COMPLETED"
            else
                echo "  Fold $f: FAILED"
                exit 1
            fi
        done
    done

else
    # -------------------------------------------------------------------------
    # 0-1 GPU: run folds sequentially
    # -------------------------------------------------------------------------
    echo "Strategy: SEQUENTIAL — running $N_FOLDS folds one at a time"
    echo "Estimated time: ~24-48h per fold on a single GPU (~5-10 days total)"
    echo "Tip: You can safely Ctrl+C and resume later with: bash run_training.sh --resume"
    echo "============================================================"

    for fold in $(seq 0 $((N_FOLDS - 1))); do
        echo ""
        echo "Starting fold $fold / $((N_FOLDS - 1))  ($(date))"
        echo "------------------------------------------------------------"

        LOG_FILE="$nnUNet_results/Dataset00${DATASET_ID}_GraftSeg/training_fold${fold}.log"
        mkdir -p "$(dirname "$LOG_FILE")"

        nnUNetv2_train \
            $DATASET_ID $CONFIG $fold \
            --npz \
            -num_epochs $N_EPOCHS \
            $RESUME_FLAG \
            2>&1 | tee "$LOG_FILE"

        echo "Fold $fold completed at $(date)"
    done
fi

# =============================================================================
# Post-training: find best configuration
# =============================================================================
echo ""
echo "============================================================"
echo "All folds complete. Finding best configuration..."
echo "============================================================"
nnUNetv2_find_best_configuration $DATASET_ID -c $CONFIG

echo ""
echo "Training complete."
echo "Results saved to: $nnUNet_results/Dataset00${DATASET_ID}_GraftSeg/"
echo ""
echo "To run inference with the 5-fold ensemble:"
echo "  nnUNetv2_predict \\"
echo "    -i \"/path/to/input/images\" \\"
echo "    -o \"/path/to/output\" \\"
echo "    -d $DATASET_ID -c $CONFIG \\"
echo "    -f 0 1 2 3 4 \\"
echo "    -chk checkpoint_best.pth"
