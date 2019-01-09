# -*- coding: utf-8 -*-
"""
Copyright ©2017. The Regents of the University of California (Regents). All Rights Reserved.
Permission to use, copy, modify, and distribute this software and its documentation for educational,
research, and not-for-profit purposes, without fee and without a signed licensing agreement, is
hereby granted, provided that the above copyright notice, this paragraph and the following two
paragraphs appear in all copies, modifications, and distributions. Contact The Office of Technology
Licensing, UC Berkeley, 2150 Shattuck Avenue, Suite 510, Berkeley, CA 94720-1620, (510) 643-
7201, otl@berkeley.edu, http://ipira.berkeley.edu/industry-info for commercial licensing opportunities.

IN NO EVENT SHALL REGENTS BE LIABLE TO ANY PARTY FOR DIRECT, INDIRECT, SPECIAL,
INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS, ARISING OUT OF
THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF REGENTS HAS BEEN
ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

REGENTS SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE. THE SOFTWARE AND ACCOMPANYING DOCUMENTATION, IF ANY, PROVIDED
HEREUNDER IS PROVIDED "AS IS". REGENTS HAS NO OBLIGATION TO PROVIDE
MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.
"""
"""
Class for analyzing a GQCNN model for grasp quality prediction
Author: Jeff Mahler
"""
import cPickle as pkl
import copy
import json
import os
import random
import sys
import time
import logging

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import scipy.misc as sm

import autolab_core.utils as utils
from autolab_core import BinaryClassificationResult, Point, TensorDataset, Logger
from autolab_core.constants import *
from perception import DepthImage
from visualization import Visualizer2D as vis2d

from gqcnn import get_gqcnn_model
from gqcnn.grasping import Grasp2D, SuctionPoint2D
from gqcnn.utils import GripperMode, ImageMode, GeneralConstants, read_pose_data

PCT_POS_VAL_FILENAME = 'pct_pos_val.npy'
TRAIN_LOSS_FILENAME = 'train_losses.npy'
TRAIN_ERRORS_FILENAME = 'train_errors.npy'
VAL_ERRORS_FILENAME = 'val_errors.npy'
TRAIN_ITERS_FILENAME = 'train_eval_iters.npy'
VAL_ITERS_FILENAME = 'val_eval_iters.npy'
WINDOW = 100
MAX_LOSS = 5.0

class GQCNNAnalyzer(object):
    """ Analyzes GQCNN models """

    def __init__(self, config, verbose=True, plot_backend='pdf'):
        """
        Parameters
        ----------
        model_dir : str
            path to the model to analyze
        output_dir : str
            path to save the model output
        config : dict
            dictionary of configuration parameters
        """
        self.cfg = config
        self.verbose = verbose

        #plt.switch_backend(plot_backend) # by default we want to use a non-interactive backend(ex. pdf) because the GQCNNAnalyzer anyways only saves plots to disk, and interactive backends sometimes cause issues with localhost when run remotely in screen 
        self._parse_config()

    def _parse_config(self):
        """ Read params from the config file """
        # plotting params
        self.log_rate = self.cfg['log_rate']
        self.font_size = self.cfg['font_size']
        self.line_width = self.cfg['line_width']
        self.dpi = self.cfg['dpi']
        self.num_bins = self.cfg['num_bins']
        self.num_vis = self.cfg['num_vis']
        
    def analyze(self, model_dir,
                output_dir,
                dataset_config=None):
        """ Analyzes a GQCNN model.

        Parameters
        ----------
        model_dir : str
            path to the model
        output_dir : str
            path to save the analysis
        dataset_config : dict
            dictionary of parameters for the dataset to test on

        Returns
        -------
        :obj:`autolab_core.BinaryClassificationResult`
            result on training data
        :obj:`autolab_core.BinaryClassificationResult`
            result on validation data
        """
        # determine model output dir
        model_name = ''
        model_root = model_dir
        while model_name == '' and model_root != '':
            model_root, model_name = os.path.split(model_root)

        model_output_dir = os.path.join(output_dir, model_name)
        if not os.path.exists(model_output_dir):
            os.mkdir(model_output_dir)

        # set up logger
        self.logger = Logger.get_logger(self.__class__.__name__, log_file=os.path.join(model_output_dir, 'analysis.log'), silence=(not self.verbose), global_log_file=self.verbose)

        self.logger.info('Analyzing model %s' %(model_name))
        self.logger.info('Saving output to %s' %(output_dir))
            
        # run predictions
        train_result, val_result = self._run_prediction_single_model(model_dir,
                                                                     model_output_dir,
                                                                     dataset_config)

        # finally plot curves
        init_train_error, final_train_error, init_train_loss, final_train_loss, init_val_error, final_val_error, norm_final_val_error = self._plot(model_dir,
                                                              model_output_dir,
                                                              train_result,
                                                              val_result)

        return train_result, val_result, init_train_error, final_train_error, init_train_loss, final_train_loss, init_val_error, final_val_error, norm_final_val_error 

    def _plot_grasp(self, datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=None):
        """ Plots a single grasp represented as a datapoint. """
        image = DepthImage(datapoint[image_field_name][:,:,0])
        depth = datapoint[pose_field_name][2]
        width = 0
        grasps = []
        if gripper_mode == GripperMode.PARALLEL_JAW or \
           gripper_mode == GripperMode.LEGACY_PARALLEL_JAW:
            if angular_preds is not None:
                num_bins = angular_preds.shape[0] / 2
                bin_width = GeneralConstants.PI / num_bins
                for i in range(num_bins):
                    bin_cent_ang = i * bin_width + bin_width / 2
                    grasps.append(Grasp2D(center=image.center, angle=GeneralConstants.PI / 2 - bin_cent_ang, depth=depth, width=0.0))
                grasps.append(Grasp2D(center=image.center, angle=datapoint[pose_field_name][3], depth=depth, width=0.0))
            else:
                grasps.append(Grasp2D(center=image.center,
                            angle=0,
                            depth=depth,
                            width=0.0))
            width = datapoint[pose_field_name][-1]
        else:
            grasps.append(SuctionPoint2D(center=image.center,
                                   axis=[1,0,0],
                                   depth=depth))                
        vis2d.imshow(image)
        for i, grasp in enumerate(grasps[:-1]):
            vis2d.grasp(grasp, width=width, color=plt.cm.RdYlGn(angular_preds[i * 2 + 1]))
        vis2d.grasp(grasps[-1], width=width, color='b')
        
    def _run_prediction_single_model(self, model_dir,
                                     model_output_dir,
                                     dataset_config):
        """ Analyze the performance of a single model. """
        # read in model config
        model_config_filename = os.path.join(model_dir, 'config.json')
        with open(model_config_filename) as data_file:
            model_config = json.load(data_file)

        # load model
        self.logger.info('Loading model %s' %(model_dir))
        log_file = None
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler):
                log_file = handler.baseFilename
        gqcnn = get_gqcnn_model(verbose=self.verbose).load(model_dir, verbose=self.verbose, log_file=log_file)
        gqcnn.open_session()
        gripper_mode = gqcnn.gripper_mode
        angular_bins = gqcnn.angular_bins

        # read params from the config
        if dataset_config is None:
            dataset_dir = model_config['dataset_dir']
            split_name = model_config['split_name']
            image_field_name = model_config['image_field_name']
            pose_field_name = model_config['pose_field_name']
            metric_name = model_config['target_metric_name']
            metric_thresh = model_config['metric_thresh']
        else:
            dataset_dir = dataset_config['dataset_dir']
            split_name = dataset_config['split_name']
            image_field_name = dataset_config['image_field_name']
            pose_field_name = dataset_config['pose_field_name']
            metric_name = dataset_config['target_metric_name']
            metric_thresh = dataset_config['metric_thresh']
            gripper_mode = dataset_config['gripper_mode']
            
        self.logger.info('Loading dataset %s' %(dataset_dir))
        dataset = TensorDataset.open(dataset_dir)
        train_indices, val_indices, _ = dataset.split(split_name)
        
        # visualize conv filters
        conv1_filters = gqcnn.filters
        num_filt = conv1_filters.shape[3]
        d = utils.sqrt_ceil(num_filt)
        vis2d.clf()
        for k in range(num_filt):
            filt = conv1_filters[:,:,0,k]
            vis2d.subplot(d,d,k+1)
            vis2d.imshow(DepthImage(filt))
            figname = os.path.join(model_output_dir, 'conv1_filters.pdf')
        vis2d.savefig(figname, dpi=self.dpi)

        # set angular bin variables
        if angular_bins > 0:
            max_angle = np.pi
            if gripper_mode == GripperMode.MULTI_SUCTION:                
                max_angle = 2*np.pi
            elif gripper_mode == GripperMode.SUCTION:
                raise ValueError('Cannot use angular bins with single suction cup!')
            bin_width = max_angle / angular_bins
        
        # aggregate training and validation true labels and predicted probabilities
        all_predictions = []
        if angular_bins > 0:
            all_predictions_raw = []
        all_labels = []
        for i in range(dataset.num_tensors):
            # log progress
            if i % self.log_rate == 0:
                self.logger.info('Predicting tensor %d of %d' %(i+1, dataset.num_tensors))

            # read in data
            image_arr = dataset.tensor(image_field_name, i).arr
            pose_arr = read_pose_data(dataset.tensor(pose_field_name, i).arr,
                                      gripper_mode)
            metric_arr = dataset.tensor(metric_name, i).arr
            label_arr = 1 * (metric_arr > metric_thresh)
            label_arr = label_arr.astype(np.uint8)
            if angular_bins > 0:
                # form mask to extract predictions from ground-truth angular bins
                raw_poses = dataset.tensor(pose_field_name, i).arr
                angles = raw_poses[:, 3]
                pred_mask = np.zeros((raw_poses.shape[0], angular_bins*2), dtype=bool)

                # form mask to extract predictions from ground-truth angular bins
                while np.any(angles < 0):
                    neg_ind = np.where(angles < 0)
                    angles[neg_ind] += max_angle
                while np.any(angles >= max_angle):
                    greater_ind = np.where(angles >= max_angle)
                    angles[greater_ind] -= max_angle
                for i in range(angles.shape[0]):
                    pred_mask[i, int((angles[i] // bin_width)*2)] = True
                    pred_mask[i, int((angles[i] // bin_width)*2 + 1)] = True

            # predict with GQ-CNN
            predictions = gqcnn.predict(image_arr, pose_arr)

            """
            for j, image in enumerate(image_arr):
                im = DepthImage(image)
                p = predictions[j,:].reshape((-1,2))


                vis2d.figure()
                vis2d.imshow(im)
                vis2d.scatter(im.center[0], im.center[1])

                for i in range(angular_bins):
                    q = p[i,1]
                    theta = i * bin_width + bin_width / 2
                    print i, theta, q
                    v = np.array([np.cos(theta), np.sin(theta)])
                    l = np.c_[im.center,
                              im.center + 10*v]
                    vis2d.plot(l[0,:], l[1,:], color=plt.cm.RdYlBu(q), linewidth=3)
                vis2d.show()

            import IPython
            IPython.embed()                
            """
            
            # reshape predictions
            if angular_bins > 0:
                raw_predictions = np.array(predictions)
                predictions = predictions[pred_mask].reshape((-1, 2))
            
            # aggregate
            all_predictions.extend(predictions[:,1].tolist())
            if angular_bins > 0:
                all_predictions_raw.extend(raw_predictions.tolist())
            all_labels.extend(label_arr.tolist())
            
        # close session
        gqcnn.close_session()            

        # create arrays
        all_predictions = np.array(all_predictions)
        all_labels = np.array(all_labels)
        train_predictions = all_predictions[train_indices]
        val_predictions = all_predictions[val_indices]
        train_labels = all_labels[train_indices]
        val_labels = all_labels[val_indices]
        if angular_bins > 0:
            all_predictions_raw = np.array(all_predictions_raw)
            train_predictions_raw = all_predictions_raw[train_indices]
            val_predictions_raw = all_predictions_raw[val_indices]        

        # aggregate results
        train_result = BinaryClassificationResult(train_predictions, train_labels)
        val_result = BinaryClassificationResult(val_predictions, val_labels)
        train_result.save(os.path.join(model_output_dir, 'train_result.cres'))
        val_result.save(os.path.join(model_output_dir, 'val_result.cres'))

        # get stats, plot curves
        self.logger.info('Model %s training error rate: %.3f' %(model_dir, train_result.error_rate))
        self.logger.info('Model %s validation error rate: %.3f' %(model_dir, val_result.error_rate))

        self.logger.info('Model %s training loss: %.3f' %(model_dir, train_result.cross_entropy_loss))
        self.logger.info('Model %s validation loss: %.3f' %(model_dir, val_result.cross_entropy_loss))

        # save images
        vis2d.figure()
        example_dir = os.path.join(model_output_dir, 'examples')
        if not os.path.exists(example_dir):
            os.mkdir(example_dir)

        # train
        self.logger.info('Saving training examples')
        train_example_dir = os.path.join(example_dir, 'train')
        if not os.path.exists(train_example_dir):
            os.mkdir(train_example_dir)
            
        # train TP
        true_positive_indices = train_result.true_positive_indices
        np.random.shuffle(true_positive_indices)
        true_positive_indices = true_positive_indices[:self.num_vis]
        for i, j in enumerate(true_positive_indices):
            k = train_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=train_predictions_raw[j])
            else:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 train_result.pred_probs[j],
                                                                 train_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(train_example_dir, 'true_positive_%03d.png' %(i)))

        # train FP
        false_positive_indices = train_result.false_positive_indices
        np.random.shuffle(false_positive_indices)
        false_positive_indices = false_positive_indices[:self.num_vis]
        for i, j in enumerate(false_positive_indices):
            k = train_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=train_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 train_result.pred_probs[j],
                                                                 train_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(train_example_dir, 'false_positive_%03d.png' %(i)))

        # train TN
        true_negative_indices = train_result.true_negative_indices
        np.random.shuffle(true_negative_indices)
        true_negative_indices = true_negative_indices[:self.num_vis]
        for i, j in enumerate(true_negative_indices):
            k = train_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=train_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 train_result.pred_probs[j],
                                                                 train_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(train_example_dir, 'true_negative_%03d.png' %(i)))

        # train TP
        false_negative_indices = train_result.false_negative_indices
        np.random.shuffle(false_negative_indices)
        false_negative_indices = false_negative_indices[:self.num_vis]
        for i, j in enumerate(false_negative_indices):
            k = train_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=train_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 train_result.pred_probs[j],
                                                                 train_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(train_example_dir, 'false_negative_%03d.png' %(i)))

        # val
        self.logger.info('Saving validation examples')
        val_example_dir = os.path.join(example_dir, 'val')
        if not os.path.exists(val_example_dir):
            os.mkdir(val_example_dir)

        # val TP
        true_positive_indices = val_result.true_positive_indices
        np.random.shuffle(true_positive_indices)
        true_positive_indices = true_positive_indices[:self.num_vis]
        for i, j in enumerate(true_positive_indices):
            k = val_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=val_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 val_result.pred_probs[j],
                                                                 val_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(val_example_dir, 'true_positive_%03d.png' %(i)))

        # val FP
        false_positive_indices = val_result.false_positive_indices
        np.random.shuffle(false_positive_indices)
        false_positive_indices = false_positive_indices[:self.num_vis]
        for i, j in enumerate(false_positive_indices):
            k = val_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=val_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 val_result.pred_probs[j],
                                                                 val_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(val_example_dir, 'false_positive_%03d.png' %(i)))

        # val TN
        true_negative_indices = val_result.true_negative_indices
        np.random.shuffle(true_negative_indices)
        true_negative_indices = true_negative_indices[:self.num_vis]
        for i, j in enumerate(true_negative_indices):
            k = val_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=val_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 val_result.pred_probs[j],
                                                                 val_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(val_example_dir, 'true_negative_%03d.png' %(i)))

        # val TP
        false_negative_indices = val_result.false_negative_indices
        np.random.shuffle(false_negative_indices)
        false_negative_indices = false_negative_indices[:self.num_vis]
        for i, j in enumerate(false_negative_indices):
            k = val_indices[j]
            datapoint = dataset.datapoint(k, field_names=[image_field_name,
                                                          pose_field_name])
            vis2d.clf()
            if angular_bins > 0:
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode, angular_preds=val_predictions_raw[j])
            else: 
                self._plot_grasp(datapoint, image_field_name, pose_field_name, gripper_mode)
            vis2d.title('Datapoint %d: Pred: %.3f Label: %.3f' %(k,
                                                                 val_result.pred_probs[j],
                                                                 val_result.labels[j]),
                        fontsize=self.font_size)
            vis2d.savefig(os.path.join(val_example_dir, 'false_negative_%03d.png' %(i)))
            
        # save summary stats
        train_summary_stats = {
            'error_rate': train_result.error_rate,
            'ap_score': train_result.ap_score,
            'auc_score': train_result.auc_score,
            'loss': train_result.cross_entropy_loss
        }
        train_stats_filename = os.path.join(model_output_dir, 'train_stats.json')
        json.dump(train_summary_stats, open(train_stats_filename, 'w'),
                  indent=JSON_INDENT,
                  sort_keys=True)

        val_summary_stats = {
            'error_rate': val_result.error_rate,
            'ap_score': val_result.ap_score,
            'auc_score': val_result.auc_score,
            'loss': val_result.cross_entropy_loss            
        }
        val_stats_filename = os.path.join(model_output_dir, 'val_stats.json')
        json.dump(val_summary_stats, open(val_stats_filename, 'w'),
                  indent=JSON_INDENT,
                  sort_keys=True)        
        
        return train_result, val_result

    def _plot(self, model_dir, model_output_dir, train_result, val_result):
        """ Plot analysis curves """
        self.logger.info('Plotting')

        _, model_name = os.path.split(model_output_dir)
        
        # set params
        colors = ['g', 'b', 'c', 'y', 'm', 'r']
        styles = ['-', '--', '-.', ':', '-'] 
        num_colors = len(colors)
        num_styles = len(styles)

        # PR, ROC
        vis2d.clf()
        train_result.precision_recall_curve(plot=True,
                                            line_width=self.line_width,
                                            color=colors[0],
                                            style=styles[0],
                                            label='TRAIN')
        val_result.precision_recall_curve(plot=True,
                                          line_width=self.line_width,
                                          color=colors[1],
                                          style=styles[1],
                                          label='VAL')
        vis2d.title('Precision Recall Curves', fontsize=self.font_size)
        handles, labels = vis2d.gca().get_legend_handles_labels()
        vis2d.legend(handles, labels, loc='best')
        figname = os.path.join(model_output_dir, 'precision_recall.png')
        vis2d.savefig(figname, dpi=self.dpi)

        vis2d.clf()
        train_result.roc_curve(plot=True,
                               line_width=self.line_width,
                               color=colors[0],
                               style=styles[0],
                               label='TRAIN')
        val_result.roc_curve(plot=True,
                             line_width=self.line_width,
                             color=colors[1],
                             style=styles[1],
                             label='VAL')
        vis2d.title('Reciever Operating Characteristic', fontsize=self.font_size)
        handles, labels = vis2d.gca().get_legend_handles_labels()
        vis2d.legend(handles, labels, loc='best')
        figname = os.path.join(model_output_dir, 'roc.png')
        vis2d.savefig(figname, dpi=self.dpi)
        
        # plot histogram of prediction errors
        num_bins = min(self.num_bins, train_result.num_datapoints)
                
        # train positives
        pos_ind = np.where(train_result.labels == 1)[0]
        diffs = np.abs(train_result.labels[pos_ind] - train_result.pred_probs[pos_ind])
        vis2d.figure()
        utils.histogram(diffs,
                        num_bins,
                        bounds=(0,1),
                        normalized=False,
                        plot=True)
        vis2d.title('Error on Positive Training Examples', fontsize=self.font_size)
        vis2d.xlabel('Abs Prediction Error', fontsize=self.font_size)
        vis2d.ylabel('Count', fontsize=self.font_size)
        figname = os.path.join(model_output_dir, 'pos_train_errors_histogram.png')
        vis2d.savefig(figname, dpi=self.dpi)

        # train negatives
        neg_ind = np.where(train_result.labels == 0)[0]
        diffs = np.abs(train_result.labels[neg_ind] - train_result.pred_probs[neg_ind])
        vis2d.figure()
        utils.histogram(diffs,
                        num_bins,
                        bounds=(0,1),
                        normalized=False,
                        plot=True)
        vis2d.title('Error on Negative Training Examples', fontsize=self.font_size)
        vis2d.xlabel('Abs Prediction Error', fontsize=self.font_size)
        vis2d.ylabel('Count', fontsize=self.font_size)
        figname = os.path.join(model_output_dir, 'neg_train_errors_histogram.png')
        vis2d.savefig(figname, dpi=self.dpi)

        # histogram of validation errors
        num_bins = min(self.num_bins, val_result.num_datapoints)

        # val positives
        pos_ind = np.where(val_result.labels == 1)[0]
        diffs = np.abs(val_result.labels[pos_ind] - val_result.pred_probs[pos_ind])
        vis2d.figure()
        utils.histogram(diffs,
                        num_bins,
                        bounds=(0,1),
                        normalized=False,
                        plot=True)
        vis2d.title('Error on Positive Validation Examples', fontsize=self.font_size)
        vis2d.xlabel('Abs Prediction Error', fontsize=self.font_size)
        vis2d.ylabel('Count', fontsize=self.font_size)
        figname = os.path.join(model_output_dir, 'pos_val_errors_histogram.png')
        vis2d.savefig(figname, dpi=self.dpi)

        # val negatives
        neg_ind = np.where(val_result.labels == 0)[0]
        diffs = np.abs(val_result.labels[neg_ind] - val_result.pred_probs[neg_ind])
        vis2d.figure()
        utils.histogram(diffs,
                        num_bins,
                        bounds=(0,1),
                        normalized=False,
                        plot=True)
        vis2d.title('Error on Negative Validation Examples', fontsize=self.font_size)
        vis2d.xlabel('Abs Prediction Error', fontsize=self.font_size)
        vis2d.ylabel('Count', fontsize=self.font_size)
        figname = os.path.join(model_output_dir, 'neg_val_errors_histogram.png')
        vis2d.savefig(figname, dpi=self.dpi)

        # losses
        try:
            train_errors_filename = os.path.join(model_dir, TRAIN_ERRORS_FILENAME)
            val_errors_filename = os.path.join(model_dir, VAL_ERRORS_FILENAME)
            train_iters_filename = os.path.join(model_dir, TRAIN_ITERS_FILENAME)
            val_iters_filename = os.path.join(model_dir, VAL_ITERS_FILENAME)
            pct_pos_val_filename = os.path.join(model_dir, PCT_POS_VAL_FILENAME)
            train_losses_filename = os.path.join(model_dir, TRAIN_LOSS_FILENAME)

            raw_train_errors = np.load(train_errors_filename)
            val_errors = np.load(val_errors_filename)
            raw_train_iters = np.load(train_iters_filename)
            val_iters = np.load(val_iters_filename)
            pct_pos_val = float(val_errors[0])
            if os.path.exists(pct_pos_val_filename):
                pct_pos_val = 100.0 * np.load(pct_pos_val_filename)
            raw_train_losses = np.load(train_losses_filename)

            val_errors = np.r_[pct_pos_val, val_errors]
            val_iters = np.r_[0, val_iters]
    
            # window the training error
            i = 0
            train_errors = []
            train_losses = []
            train_iters = []
            while i < raw_train_errors.shape[0]:
                train_errors.append(np.mean(raw_train_errors[i:i+WINDOW]))
                train_losses.append(np.mean(raw_train_losses[i:i+WINDOW]))
                train_iters.append(i)
                i += WINDOW
            train_errors = np.array(train_errors)
            train_losses = np.array(train_losses)
            train_iters = np.array(train_iters)
        
            init_val_error = val_errors[0]
            norm_train_errors = train_errors / init_val_error
            norm_val_errors = val_errors / init_val_error
            norm_final_val_error = val_result.error_rate / val_errors[0]
            if pct_pos_val > 0:
                norm_final_val_error = val_result.error_rate / pct_pos_val        
    
            vis2d.clf()
            vis2d.plot(train_iters, train_errors, linewidth=self.line_width, color='b')
            vis2d.plot(val_iters, val_errors, linewidth=self.line_width, color='g')
            vis2d.ylim(0, 100)
            vis2d.legend(('TRAIN (Minibatch)', 'VAL'), fontsize=self.font_size, loc='best')
            vis2d.xlabel('Iteration', fontsize=self.font_size)
            vis2d.ylabel('Error Rate', fontsize=self.font_size)
            vis2d.title('Error Rate vs Training Iteration', fontsize=self.font_size)
            figname = os.path.join(model_output_dir, 'training_error_rates.png')
            vis2d.savefig(figname, dpi=self.dpi)
            
            vis2d.clf()
            vis2d.plot(train_iters, norm_train_errors, linewidth=4, color='b')
            vis2d.plot(val_iters, norm_val_errors, linewidth=4, color='g')
            vis2d.ylim(0, 2.0)
            vis2d.legend(('TRAIN (Minibatch)', 'VAL'), fontsize=self.font_size, loc='best')
            vis2d.xlabel('Iteration', fontsize=self.font_size)
            vis2d.ylabel('Normalized Error Rate', fontsize=self.font_size)
            vis2d.title('Normalized Error Rate vs Training Iteration', fontsize=self.font_size)
            figname = os.path.join(model_output_dir, 'training_norm_error_rates.png')
            vis2d.savefig(figname, dpi=self.dpi)

            train_losses[train_losses > MAX_LOSS] = MAX_LOSS # CAP LOSSES
            vis2d.clf()
            vis2d.plot(train_iters, train_losses, linewidth=self.line_width, color='b')
            vis2d.ylim(0, 2.0)
            vis2d.xlabel('Iteration', fontsize=self.font_size)
            vis2d.ylabel('Loss', fontsize=self.font_size)
            vis2d.title('Training Loss vs Iteration', fontsize=self.font_size)
            figname = os.path.join(model_output_dir, 'training_losses.png')
            vis2d.savefig(figname, dpi=self.dpi)
            
            # log
            self.logger.info('TRAIN')
            self.logger.info('Original error: %.3f' %(train_errors[0]))
            self.logger.info('Final error: %.3f' %(train_result.error_rate))
            self.logger.info('Orig loss: %.3f' %(train_losses[0]))
            self.logger.info('Final loss: %.3f' %(train_losses[-1]))
            
            self.logger.info('VAL')
            self.logger.info('Original error: %.3f' %(pct_pos_val))
            self.logger.info('Final error: %.3f' %(val_result.error_rate))
            self.logger.info('Normalized error: %.3f' %(norm_final_val_error))

            return train_errors[0], train_result.error_rate, train_losses[0], train_losses[-1], pct_pos_val, val_result.error_rate, norm_final_val_error
        except Exception as e:
            self.logger.error('Failed to plot training curves!\n' + str(e))
