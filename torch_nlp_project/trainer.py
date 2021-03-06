from datetime import datetime
from typing import Iterable, Union

import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from torchtext.data import Iterator

from . import models
from .config import TrainConfig
from .data_loader import DataLoader
from .utils import get_logger


class TrainManager:
    def __init__(self, training_config_path: str, device: Union[str, torch.device]):
        """
        :param training_config_path: (str) training config file path.
        :param device: (str, torch.device) device for training.
        """
        # Set loogger
        self.logger = get_logger(f"{datetime.now().strftime('%Y-%m-%d_%H:%M:%S')}_train.log")
        self.logger.info("Setting logger is complete")

        # Set device
        self.device = torch.device(device)
        self.logger.info(f"Setting device:{self.device} is complete")

        # Load training Config
        self.config = TrainConfig.from_json(training_config_path)
        self.logger.info(f"Loaded training config from '{training_config_path}'")

        # Load Dataset
        self.data_loader = DataLoader.load(self.config.data_loader_path)
        self.train_dataset, self.test_dataset = self.data_loader.train_dataset, self.data_loader.test_dataset
        self.logger.info("Loaded training, test datasets")

        # Create Model
        self.model = getattr(models, self.config.model_type)(**self.config.model_configs)
        self.model.to(self.device)
        self.logger.info(f"Prepared model type: {type(self.model)}")

    def train(self):
        """
        Start training.
        """
        self.logger.info("------------- Training Start -------------")

        # Set optimizer
        optimizer = getattr(optim, self.config.optimizer)(
            self.model.parameters(), lr=self.config.learning_rate, **self.config.optimizer_args
        )

        for epoch in range(1, self.config.epoch + 1):

            # Shape
            # batch.text: (Sequence x BatchSize)
            # batch.label: (BatchSize)
            criterion = nn.CrossEntropyLoss()
            batches = Iterator(self.train_dataset, batch_size=self.config.batch_size, device=self.device, train=True)

            # Training
            loss_sum = 0.0
            pred_labels = []
            true_labels = []
            total_step = int(len(self.train_dataset) / self.config.batch_size + 1)
            for step_num, batch in enumerate(batches):
                self.model.train()
                optimizer.zero_grad()

                # Shape
                # output: (BatchSize)
                output = self.model(batch.text)

                loss = criterion(output, batch.label)
                loss.backward()
                optimizer.step()

                loss_sum += loss.item()

                pred_labels.extend(output.argmax(dim=1).cpu().detach().numpy())
                true_labels.extend(batch.label.cpu().numpy())

                # logging step
                if (step_num + 1) % self.config.steps_per_log == 0:
                    accuracy, precision, recall, f1 = self.get_metrics(true_labels, pred_labels)
                    self.logger.info(
                        f"{epoch:4} epoches {step_num + 1:6} / {total_step:-6} steps, "
                        f"average loss: {loss_sum / (self.config.steps_per_log):8.5}, "
                        f"accuracy: {accuracy:7.4}, "
                        f"precision: {precision:7.4}, "
                        f"recall: {recall:7.4}, "
                        f"f1: {f1:7.4}"
                    )
                    loss_sum = 0.0
                    pred_labels = []
                    true_labels = []

                # evaluation step
                if (step_num + 1) % self.config.steps_per_eval == 0:
                    # Evaluate
                    eval_loss, accuracy, precision, recall, f1 = self._evaluate()
                    self.logger.info(
                        f"{epoch:4} epoches {step_num + 1:6} / {total_step:-6} steps, "
                        f"average evaluate loss: {eval_loss:6.3}, "
                        f"accuracy: {accuracy:7.4}, "
                        f"precision: {precision:7.4}, "
                        f"recall: {recall:7.4}, "
                        f"f1: {f1:7.4}"
                    )

                    self.model.save(f"{self.config.model_save_prefix}_{epoch}epoch_{step_num + 1}steps_f1-{f1}.pth")

            # Evaluate
            eval_loss, accuracy, precision, recall, f1 = self._evaluate()
            self.logger.info(
                f"{epoch:4} epoches average evaluate loss: {eval_loss:6.3}, "
                f"accuracy: {accuracy:7.4}, "
                f"precision: {precision:7.4}, "
                f"recall: {recall:7.4}, "
                f"f1: {f1:7.4}"
            )

            self.model.save(f"{self.config.model_save_prefix}_{epoch}epoch_f1-{f1}.pth")

    def _evaluate(self):
        """
        Evaluate current model using test dataset.
        """
        self.model.eval()

        batches = Iterator(
            self.test_dataset,
            batch_size=self.config.val_batch_size,
            device=self.device,
            sort_key=lambda x: len(x.text),
            train=False,
        )
        criterion = nn.CrossEntropyLoss()

        loss_sum = 0.0
        true_labels = []
        pred_labels = []
        for batch in batches:
            output = self.model(batch.text)

            # Calculate loss
            loss = criterion(output, batch.label)
            loss_sum += loss.item() * len(batch)

            # Calculate metrics
            true_labels.extend(batch.label.cpu().numpy())
            pred_labels.extend(output.argmax(dim=1).cpu().detach().numpy())

        accuracy, precision, recall, f1 = self.get_metrics(true_labels, pred_labels)

        return loss_sum / len(self.test_dataset), accuracy, precision, recall, f1

    def get_metrics(self, true_labels: Iterable[int], pred_labels: Iterable[int]):
        """
        :param true_labels: (iterable) correct label.
        :param pred_labels: (iterable) predicted label by model.
        :return: (accuracy, precision, recall, f1) (tuple of float)
        """
        accuracy = accuracy_score(true_labels, pred_labels)
        precision, recall, f1, _ = precision_recall_fscore_support(
            true_labels, pred_labels, average="binary", zero_division=0
        )
        return accuracy, precision, recall, f1
