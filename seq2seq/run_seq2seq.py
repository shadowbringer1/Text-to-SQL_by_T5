# Set up logging
import sys
import logging

# ==============================Change Path=======================================
import os

abs_path = os.path.abspath(sys.argv[0])
dir_path = abs_path.replace("/seq2seq/run_seq2seq.py", "")
sys.path.append(dir_path)
# ================================================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
    datefmt="%m/%d/%Y %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    level=logging.WARNING,
)
logger = logging.getLogger(__name__)

import os
import json
from pathlib import Path
from contextlib import nullcontext
from dataclasses import asdict, fields
from transformers.hf_argparser import HfArgumentParser
from transformers.training_args_seq2seq import Seq2SeqTrainingArguments
from transformers.models.auto import AutoConfig, AutoTokenizer, AutoModelForSeq2SeqLM
# from transformers.data.data_collator import DataCollatorForSeq2Seq
from seq2seq.utils.relation_data_collator import DataCollatorForSeq2Seq
from transformers.trainer_utils import get_last_checkpoint, set_seed
from transformers.models.t5.modeling_t5 import T5ForConditionalGeneration
from transformers.models.t5.tokenization_t5_fast import T5TokenizerFast
from transformers.tokenization_utils_fast import PreTrainedTokenizerFast
from tokenizers import AddedToken
from seq2seq.utils.args import ModelArguments
from seq2seq.utils.picard_model_wrapper import PicardArguments, PicardLauncher, with_picard
from seq2seq.utils.dataset import DataTrainingArguments, DataArguments
from seq2seq.utils.dataset_loader import load_dataset
from seq2seq.utils.spider import SpiderTrainer
from seq2seq.utils.cosql import CoSQLTrainer
from seq2seq.utils.sparc import SParCTrainer
from seq2seq.preprocess.get_relation2id_dict import get_relation2id_dict

from model.model_utils import get_relation_t5_model, get_original_t5_model
from transformers import T5Config



def main() -> None:
    # See all possible arguments by passing the --help flag to this script.
    parser = HfArgumentParser(
        (PicardArguments, ModelArguments, DataArguments, DataTrainingArguments, Seq2SeqTrainingArguments)
    )
    picard_args: PicardArguments
    model_args: ModelArguments
    data_args: DataArguments
    data_training_args: DataTrainingArguments
    training_args: Seq2SeqTrainingArguments
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        picard_args, model_args, data_args, data_training_args, training_args = parser.parse_json_file(
            json_file=os.path.abspath(sys.argv[1])
        )
    elif len(sys.argv) == 3 and sys.argv[1].startswith("--local_rank") and sys.argv[2].endswith(".json"):
        data = json.loads(Path(os.path.abspath(sys.argv[2])).read_text())
        data.update({"local_rank": int(sys.argv[1].split("=")[1])})
        picard_args, model_args, data_args, data_training_args, training_args = parser.parse_dict(args=data)
    else:
        picard_args, model_args, data_args, data_training_args, training_args = parser.parse_args_into_dataclasses()
    
    # If model_name_or_path includes ??? instead of the number of steps, 
    # we load the latest checkpoint.
    if 'checkpoint-???' in model_args.model_name_or_path:
        model_args.model_name_or_path = get_last_checkpoint(
            os.path.dirname(model_args.model_name_or_path))
        logger.info(f"Resolve model_name_or_path to {model_args.model_name_or_path}")

    combined_args_dict = {
        **asdict(picard_args),
        **asdict(model_args),
        **asdict(data_args),
        **asdict(data_training_args),
        **training_args.to_sanitized_dict(),
    }
    combined_args_dict.pop("local_rank", None)

    if "wandb" in training_args.report_to and training_args.local_rank <= 0:
        import wandb

        init_args = {}
        if "MLFLOW_EXPERIMENT_ID" in os.environ:
            init_args["group"] = os.environ["MLFLOW_EXPERIMENT_ID"]
        wandb.init(
            project=os.getenv("WANDB_PROJECT", data_training_args.wandb_project_name),
            name=training_args.run_name,
            **init_args,
        )
        wandb.config.update(combined_args_dict, allow_val_change=True)

    if not training_args.do_train and not training_args.do_eval and not training_args.do_predict:
        logger.info("There is nothing to do. Please pass `do_train`, `do_eval` and/or `do_predict`.")
        return

    # Detect last checkpoint
    last_checkpoint = None
    if os.path.isdir(training_args.output_dir) and training_args.do_train and not training_args.overwrite_output_dir:
        last_checkpoint = get_last_checkpoint(training_args.output_dir)
        if last_checkpoint is None and len(os.listdir(training_args.output_dir)) > 0:
            raise ValueError(
                f"Output directory ({training_args.output_dir}) already exists and is not empty. "
                "Use --overwrite_output_dir to overcome."
            )
        elif last_checkpoint is not None and training_args.resume_from_checkpoint is None:
            logger.info(
                f"Checkpoint detected, resuming training at {last_checkpoint}. To avoid this behavior, change "
                "the `--output_dir` or add `--overwrite_output_dir` to train from scratch."
            )

    os.makedirs(training_args.output_dir, exist_ok=True)

    if training_args.local_rank <= 0:
        with open(f"{training_args.output_dir}/combined_args.json", "w") as f:
            json.dump(combined_args_dict, f, indent=4)

    # Initialize random number generators
    set_seed(training_args.seed)

    # Initialize config
    config = AutoConfig.from_pretrained(
        model_args.config_name if model_args.config_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
        max_length=data_training_args.max_target_length,
        num_beams=data_training_args.num_beams,
        num_beam_groups=data_training_args.num_beam_groups,
        diversity_penalty=data_training_args.diversity_penalty,
        gradient_checkpointing=training_args.gradient_checkpointing,
        use_cache=not training_args.gradient_checkpointing,
    )

    # Initialize tokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        model_args.tokenizer_name if model_args.tokenizer_name else model_args.model_name_or_path,
        cache_dir=model_args.cache_dir,
        use_fast=model_args.use_fast_tokenizer,
        revision=model_args.model_revision,
        use_auth_token=True if model_args.use_auth_token else None,
    )
    assert isinstance(tokenizer, PreTrainedTokenizerFast), "Only fast tokenizers are currently supported"
    if isinstance(tokenizer, T5TokenizerFast):
        # In T5 `<` is OOV, see https://github.com/google-research/language/blob/master/language/nqg/tasks/spider/restore_oov.py
        tokenizer.add_tokens([AddedToken(" <="), AddedToken(" <")])

    # Load dataset
    metric, dataset_splits = load_dataset(
        data_args=data_args,
        model_args=model_args,
        data_training_args=data_training_args,
        training_args=training_args,
        tokenizer=tokenizer,
    )

    # Initialize Picard if necessary
    with PicardLauncher() if picard_args.launch_picard and training_args.local_rank <= 0 else nullcontext(None):
        # Get Picard model class wrapper
        if picard_args.use_picard:
            model_cls_wrapper = lambda model_cls: with_picard(
                model_cls=model_cls, picard_args=picard_args, tokenizer=tokenizer, schemas=dataset_splits.schemas
            )
        else:
            model_cls_wrapper = lambda model_cls: model_cls

        # Initialize model
        if data_training_args.use_rasat:
            _, _, num_relations = get_relation2id_dict(data_training_args.edge_type, use_coref=data_training_args.use_coref, use_dependency=data_training_args.use_dependency)
            config.num_relations = num_relations
            print("===================================================")
            print("Num of relations uesd in RASAT is : ", num_relations)
            print("===================================================")
            print("Use relation model.")
            print(model_args.model_name_or_path)
            model = get_relation_t5_model(config=config, model_name_or_path=model_args.model_name_or_path)
        else:
            print("Use original model.")
            model = get_original_t5_model(config=config, model_name_or_path=model_args.model_name_or_path)

        if isinstance(model, T5ForConditionalGeneration):
            print("True")
        model.resize_token_embeddings(len(tokenizer))

        if training_args.label_smoothing_factor > 0 and not hasattr(model, "prepare_decoder_input_ids_from_labels"):
            logger.warning(
                "label_smoothing is enabled but the `prepare_decoder_input_ids_from_labels` method is not defined for"
                f"`{model.__class__.__name__}`. This will lead to loss being calculated twice and will take up more memory"
            )

        # Initialize Trainer
        trainer_kwargs = {
            "model": model,
            "args": training_args,
            "metric": metric,
            "train_dataset": dataset_splits.train_split.dataset if training_args.do_train else None,
            "eval_dataset": dataset_splits.eval_split.dataset if training_args.do_eval else None,
            "eval_examples": dataset_splits.eval_split.examples if training_args.do_eval else None,
            "tokenizer": tokenizer,
            "data_collator": DataCollatorForSeq2Seq(
                tokenizer,
                model=model,
                label_pad_token_id=(-100 if data_training_args.ignore_pad_token_for_loss else tokenizer.pad_token_id),
                pad_to_multiple_of=8 if training_args.fp16 else None,
            ),
            "ignore_pad_token_for_loss": data_training_args.ignore_pad_token_for_loss,
            "target_with_db_id": data_training_args.target_with_db_id,
        }
        if data_args.dataset in ["spider"]:
            trainer = SpiderTrainer(**trainer_kwargs)
        elif data_args.dataset in ["cosql", "cosql+spider"]:
            trainer = CoSQLTrainer(**trainer_kwargs)
        elif data_args.dataset in ["sparc", "sparc+spider"]:
            trainer = SParCTrainer(**trainer_kwargs)
        else:
            raise NotImplementedError()

        # Training
        if training_args.do_train:
            logger.info("*** Train ***")

            checkpoint = None

            if training_args.resume_from_checkpoint is not None:
                checkpoint = training_args.resume_from_checkpoint
            elif last_checkpoint is not None:
                checkpoint = last_checkpoint

            train_result = trainer.train(resume_from_checkpoint=checkpoint)
            trainer.save_model()  # Saves the tokenizer too for easy upload

            metrics = train_result.metrics
            max_train_samples = (
                data_training_args.max_train_samples
                if data_training_args.max_train_samples is not None
                else len(dataset_splits.train_split.dataset)
            )
            metrics["train_samples"] = min(max_train_samples, len(dataset_splits.train_split.dataset))

            trainer.log_metrics("train", metrics)
            trainer.save_metrics("train", metrics)
            trainer.save_state()

        # Evaluation
        if training_args.do_eval:
            logger.info("*** Evaluate ***")

            metrics = trainer.evaluate(
                max_length=data_training_args.val_max_target_length,
                max_time=data_training_args.val_max_time,
                num_beams=data_training_args.num_beams,
                metric_key_prefix="eval",
            )
            max_val_samples = (
                data_training_args.max_val_samples
                if data_training_args.max_val_samples is not None
                else len(dataset_splits.eval_split.dataset)
            )
            metrics["eval_samples"] = min(max_val_samples, len(dataset_splits.eval_split.dataset))

            trainer.log_metrics("eval", metrics)
            trainer.save_metrics("eval", metrics)

        # Testing
        if training_args.do_predict:
            logger.info("*** Predict ***")
            for section, test_split in dataset_splits.test_splits.items():
                results = trainer.predict(
                    test_split.dataset, 
                    test_split.examples,
                    max_length=data_training_args.val_max_target_length,
                    max_time=data_training_args.val_max_time,
                    num_beams=data_training_args.num_beams,
                    metric_key_prefix=section)
                metrics = results.metrics

                metrics[f"{section}_samples"] = len(test_split.dataset)

                trainer.log_metrics(section, metrics)
                trainer.save_metrics(section, metrics)


if __name__ == "__main__":
    main()
