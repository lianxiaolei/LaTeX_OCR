import os
import time

import tensorflow as tf

from .utils.general import init_dir, get_logger


class BaseModel(object):
  """Generic class for tf models"""

  def __init__(self, config, dir_output):
    """Defines self._config

    Args:
        config: (Config instance) class with hyper parameters, vocab and embeddings

    """
    self._config = config
    self._dir_output = dir_output
    init_dir(self._dir_output)
    self.logger = get_logger(self._dir_output + "model.log")
    tf.reset_default_graph()  # saveguard if previous model was defined

  def build_train(self, config=None):
    """To overwrite with model-specific logic

    This logic must define
        - self.loss
        - self.lr
        - etc.
    """
    raise NotImplementedError

  def build_pred(self, config=None):
    """Similar to build_train but no need to define train_op"""
    raise NotImplementedError

  def _add_train_op(self, lr_method, lr, loss, clip=-1):
    """Defines self.train_op that performs an update on a batch

    Args:
        lr_method: (string) sgd method, for example "adam"
        lr: (tf.placeholder) tf.float32, learning rate
        loss: (tensor) tf.float32 loss to minimize
        clip: (python float) clipping of gradient. If < 0, no clipping

    """
    _lr_m = lr_method.lower()  # lower to make sure

    with tf.variable_scope("train_step"):
      if _lr_m == 'adam':  # sgd method
        optimizer = tf.train.AdamOptimizer(lr)
      elif _lr_m == 'adagrad':
        optimizer = tf.train.AdagradOptimizer(lr)
      elif _lr_m == 'sgd':
        optimizer = tf.train.GradientDescentOptimizer(lr)
      elif _lr_m == 'rmsprop':
        optimizer = tf.train.RMSPropOptimizer(lr)
      else:
        raise NotImplementedError("Unknown method {}".format(_lr_m))

      # for batch norm beta gamma
      update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
      with tf.control_dependencies(update_ops):
        if clip > 0:  # gradient clipping if clip is positive
          grads, vs = zip(*optimizer.compute_gradients(loss))
          grads, gnorm = tf.clip_by_global_norm(grads, clip)
          self.train_op = optimizer.apply_gradients(zip(grads, vs))
        else:
          self.train_op = optimizer.minimize(loss)

  def init_session(self):
    """Defines self.sess, self.saver and initialize the variables"""
    self.sess = tf.Session()  # config=tf.ConfigProto(log_device_placement=True))
    self.sess.run(tf.global_variables_initializer())
    self.saver = tf.train.Saver(max_to_keep=1)
    dir_model = self._dir_output + "model_weights/"
    init_dir(dir_model)
    self.ckeck_point = tf.train.latest_checkpoint(dir_model)
    print("checkpoint", self.ckeck_point)
    self.startepoch = 0
    if self.ckeck_point != None:
      self.saver.restore(self.sess, self.ckeck_point)
      idx = self.ckeck_point.find("-")
      self.startepoch = int(self.ckeck_point[idx + 1:])
      print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! find a checkpoint, load epoch", self.startepoch)
    self._add_summary()  # tensorboard 可视化

  def restore_session(self, dir_model):
    """Reload weights into session

    Args:
        sess: tf.Session()
        dir_model: dir with weights

    """
    self.logger.info("Reloading the latest trained model...")
    self.saver.restore(self.sess, dir_model)

  def save_session(self, epoch):
    """Saves session"""
    # check dir one last time
    dir_model = self._dir_output + "model_weights/"
    init_dir(dir_model)

    self.logger.info("- Saving model...")
    self.saver.save(self.sess, dir_model + "model.cpkt", global_step=epoch)
    self.logger.info("- Saved model in {}".format(dir_model))

  def save_debug_session(self, epoch, i):
    """Saves session"""
    # check dir one last time
    dir_model = self._dir_output + "debug_model_weights/"
    init_dir(dir_model)

    self.logger.info("- Saving model...")
    self.saver.save(self.sess, dir_model + "model_" + str(i) + ".cpkt", global_step=epoch)
    self.logger.info("- Saved model in {}".format(dir_model))

  def close_session(self):
    """Closes the session"""
    self.sess.close()

  def _add_summary(self):
    """Defines variables for Tensorboard

    Args:
        dir_output: (string) where the results are written

    """
    self.merged = tf.summary.merge_all()
    self.file_writer = tf.summary.FileWriter(self._dir_output, self.sess.graph)

  def train(self, config, train_set, val_set, lr_schedule):
    """Global training procedure

    Calls method self.run_epoch and saves weights if score improves.
    All the epoch-logic including the lr_schedule update must be done in
    self.run_epoch

    Args:
        config: Config instance contains params as attributes
        train_set: Dataset instance
        val_set: Dataset instance
        lr_schedule: LRSchedule instance that takes care of learning proc

    Returns:
        best_score: (float)

    """
    best_score = None

    for epoch in range(config.n_epochs):
      if epoch < self.startepoch:
        continue

      # logging
      tic = time.time()
      self.logger.info("Epoch {:}/{:}".format(epoch + 1, config.n_epochs))

      # epoch
      score = self._run_epoch(config, train_set, val_set, epoch, lr_schedule)

      # save weights if we have new best score on eval
      if best_score is None or score >= best_score:
        best_score = score
        self.logger.info("- New best score ({:04.2f})!".format(best_score))
        self.save_session(epoch)
      if lr_schedule.stop_training:
        self.logger.info("- Early Stopping.")
        break

      # logging
      toc = time.time()
      self.logger.info("- Elapsed time: {:04.2f}, learning rate: {:04.5f}".format(toc - tic, lr_schedule.lr))

    return best_score

  def _run_epoch(config, train_set, val_set, epoch, lr_schedule):
    """Model_specific method to overwrite

    Performs an epoch of training

    Args:
        config: Config
        train_set: Dataset instance
        val_set: Dataset instance
        epoch: (int) id of the epoch, starting at 0
        lr_schedule: LRSchedule instance that takes care of learning proc

    Returns:
        score: (float) model will select weights that achieve the highest
            score

    """
    raise NotImplementedError

  def evaluate(self, config, test_set):
    """Evaluates model on test set

    Calls method run_evaluate on test_set and takes care of logging

    Args:
        config: Config
        test_set: instance of class Dataset

    Return:
        scores: (dict) scores["acc"] = 0.85 for instance

    """
    self.logger.info("- Evaluating...")
    scores = self._run_evaluate(config, test_set)  # evaluate
    msg = " ... ".join([" {} is {:04.2f} ".format(k, v) for k, v in scores.items()])
    self.logger.info("- Eval: {}".format(msg))

    return scores

  def _run_evaluate(config, test_set):
    """Model-specific method to overwrite

    Performs an epoch of evaluation

    Args:
        config: Config
        test_set: Dataset instance

    Returns:
        scores: (dict) scores["acc"] = 0.85 for instance

    """
    raise NotImplementedError
