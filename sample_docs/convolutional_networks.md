# Convolutional Neural Networks

## Overview

A Convolutional Neural Network (CNN) is a deep learning architecture designed for processing data with a grid-like topology, most notably images. CNNs exploit the spatial structure of their input by applying learned filters across local regions, making them far more parameter-efficient than fully connected networks for visual tasks.

## Core Operations

### Convolution

A convolutional layer slides a small filter (kernel) across the input, computing a dot product at each position. This produces a feature map that captures where a particular pattern appears in the input. Key properties:

- **Local connectivity**: each output neuron connects to only a small patch of the input.
- **Weight sharing**: the same filter weights are applied at every spatial position, dramatically reducing the number of parameters.
- **Translation equivariance**: a pattern detected in one part of the image will be detected wherever it appears.

Common filter sizes are 3×3 and 5×5. A layer typically learns dozens to hundreds of filters in parallel, each detecting a different feature.

### Pooling

Pooling layers reduce the spatial dimensions of feature maps, providing a form of downsampling and local invariance. Max pooling takes the maximum value within each pooling window (typically 2×2), while average pooling takes the mean. The result is a coarser representation that is less sensitive to the exact position of detected features.

### Stride and Padding

- **Stride**: the step size between filter positions. A stride of 2 halves the spatial dimensions without a separate pooling layer.
- **Padding**: adding zeros around the input border allows the filter to reach edge pixels and can preserve spatial dimensions across a layer.

## Typical Architecture

A standard CNN stacks convolutional and pooling layers to build a hierarchy of feature detectors, then flattens the result into a vector fed to fully connected layers for classification:

1. Input image (e.g., 224×224×3)
2. Conv + ReLU → low-level features (edges, textures)
3. Pool → spatial downsampling
4. Conv + ReLU → mid-level features (shapes, patterns)
5. Pool → further downsampling
6. Flatten → vector representation
7. Fully connected + Softmax → class probabilities

## Landmark Architectures

- **LeNet-5** (LeCun, 1998): pioneered CNNs for handwritten digit recognition using 7 layers and 60K parameters.
- **AlexNet** (2012): 8 layers, 60M parameters, first to win ImageNet by a large margin; introduced ReLU and dropout to deep CNNs.
- **VGGNet** (2014): demonstrated that depth using only 3×3 convolutions is key to performance.
- **ResNet** (2015): introduced residual (skip) connections that allow training networks over 100 layers deep by addressing the vanishing gradient problem.
- **EfficientNet** (2019): scales depth, width, and resolution together via a compound coefficient, achieving state-of-the-art accuracy at lower compute.

## Receptive Field

The receptive field of a neuron is the region of the input that influences its output. Stacking convolutional layers increases the effective receptive field exponentially, allowing deeper neurons to integrate information from large areas of the image while earlier neurons respond to fine-grained local details.

## Applications

- **Image Classification**: assigning a label to an entire image (ResNet, EfficientNet).
- **Object Detection**: localizing and labeling multiple objects in one pass (YOLO, Faster R-CNN).
- **Semantic Segmentation**: classifying each pixel into a category (U-Net, DeepLab).
- **Face Recognition**: mapping face images to compact embeddings for identity matching.
- **Medical Imaging**: detecting tumors in radiology scans, classifying skin lesions.

## Relationship to Other Architectures

CNNs share the concept of hierarchical feature extraction with other deep learning models. Recurrent Neural Networks process sequences rather than grids. The Transformer architecture, while dominant in natural language processing, has also been adapted for vision (Vision Transformer, ViT), processing images as sequences of patches rather than applying convolutions.
