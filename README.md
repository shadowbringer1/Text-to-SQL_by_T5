# Text-to-SQL by T5


# Quick start

## Code downloading
This repository uses git submodules. Clone it like this:

```
$ git clone https://github.com/shadowbringer1/Text-to-SQL_by_T5.git
$ cd Text-to-SQL_by_T5
$ git submodule update --init --recursive
```
## Download the dataset
Before running the code, you should download dataset files.

First, you should create a dictionary like this:
```
mkdir -p dataset_files/ori_dataset
```

And then you need to download the dataset file to dataset_files/ and just keep it in zip format. The download link are here:
+ Spider, [link](https://drive.google.com/uc?export=download&id=1_AckYkinAnhqmRQtGsQgUKAnTHxxX5J0)

Then unzip those dataset files into dataset_files/ori_dataset. Both files in zip format and unzip format is needed:

```
unzip dataset_files/spider.zip -d dataset_files/ori_dataset/
```

## The Coreference Resolution Files
We recommend you just use the generated coreference resolution files. It just needs you run

```
unzip preprocessed_dataset.zip -d ./dataset_files/
```

## Environment setup

### Use docker
The best performance is achieved by exploiting PICARD[1], and if you want to reproduce it, we recommend you use Docker.

You can simply use 
```
make eval
```
to start a new docker container for an interaction terminal that supports PICARD. 

Since the docker environment doesn't have stanza, so you should run these commands before training or evaluting:
```
pip install stanza
python3 seq2seq/stanza_downloader.py
```

**Note:We only use PICARD for seperately evalutaion.**

### Do not use Docker
If Docker is not available to you, you could also run it in a python 3.9.7 environment 

```bash
conda create -n rasat python=3.9.7
conda activate rasat
pip3 install torch==1.8.2 torchvision==0.9.2 torchaudio==0.8.2 --extra-index-url https://download.pytorch.org/whl/lts/1.8/cu111
pip install -r requirements.txt
```

However, you could not use PICARD in that way.

**Please Note: the version of stanza must keep 1.3.0, other versions will lead to error. **




## Training

You can simply run these code like this:

```
CUDA_VISIBLE_DEVICES="0" python3 seq2seq/run_seq2seq.py configs/spider/train_spider_rasat_3b.json
```


## Evalutaion

You can simply run these codes:

```
CUDA_VISIBLE_DEVICES="0" python3 seq2seq/eval_run_seq2seq.py configs/spider/eval_spider_rasat_2624.json
```

Notice：If you use Docker for evaluation, you may need to change the filemode for these dictionary before starting a new docker container:

```
chmod -R 777 seq2seq/
chmod -R 777 dataset_files/
```


# Checkpoint

You can load checkpoints  [here](https://huggingface.co/Jiexing/spider_relation_t5_3b-2624).
