# Neural Networks: A Primer

## Overview

A neural network is a computational model loosely inspired by the structure of the human brain. It consists of layers of interconnected nodes, or neurons, that process information by passing signals through weighted connections. Neural networks are the foundation of modern deep learning and have achieved breakthrough results in image recognition, natural language processing, and game playing.

## Key Concepts

### Neurons and Activation Functions

Each neuron receives one or more inputs, multiplies them by learned weights, sums the results, and applies an activation function to produce an output. Common activation functions include:

- **ReLU (Rectified Linear Unit)**: outputs the input directly if positive, otherwise zero. It is the most widely used activation function in hidden layers due to its simplicity and effectiveness at reducing the vanishing gradient problem.
- **Sigmoid**: squashes any input into the range (0, 1), historically used in output layers for binary classification.
- **Softmax**: generalizes sigmoid to multiple classes, producing a probability distribution over outputs.

### Layers

Neural networks are organized into three types of layers:

1. **Input layer**: receives raw data such as pixel values, token embeddings, or sensor readings.
2. **Hidden layers**: perform intermediate transformations. The depth (number of hidden layers) gives rise to the term "deep learning."
3. **Output layer**: produces the final prediction — a class label, a continuous value, or a probability distribution.

### Weights and Biases

Every connection between neurons carries a weight that scales the signal. Each neuron also has a bias term that shifts its activation. These parameters are learned during training and encode the knowledge the network has extracted from data.

## Training

### Loss Functions

A loss function measures how far the network's predictions are from the ground truth. Common choices include:

- **Mean Squared Error (MSE)**: used for regression tasks.
- **Cross-Entropy Loss**: used for classification tasks; penalizes confident wrong predictions heavily.

### Gradient Descent

Training a neural network means minimizing the loss function with respect to all weights and biases. Gradient descent does this iteratively: compute the gradient of the loss with respect to each parameter, then nudge each parameter in the direction that reduces the loss.

The update rule is: `weight = weight - learning_rate * gradient`

The learning rate controls the step size. Too large and training diverges; too small and it converges slowly.

### Backpropagation

Backpropagation is the algorithm used to compute gradients efficiently. It applies the chain rule of calculus, propagating the error signal backwards from the output layer through each hidden layer to the input. This allows every weight in the network to receive a gradient signal indicating how it should change to reduce the loss.

### Mini-Batch Training

In practice, gradients are computed on small random subsets of the training data called mini-batches rather than the full dataset. This approach — called stochastic gradient descent (SGD) — introduces noise that can help escape local minima and dramatically reduces the memory required per update.

## Architectures

### Feedforward Networks (MLP)

The simplest architecture: information flows in one direction from input to output with no cycles. Also called a Multi-Layer Perceptron (MLP). Effective for tabular data and simple classification tasks.

### Convolutional Neural Networks (CNN)

CNNs are specialized for grid-structured data such as images. Convolutional layers apply learned filters across spatial positions, allowing the network to detect local patterns regardless of their position in the image. Pooling layers downsample the representation, building hierarchical feature detectors from edges up to complex objects.

### Recurrent Neural Networks (RNN)

RNNs process sequential data by maintaining a hidden state that is updated at each time step. They are suited to tasks like language modeling and time series forecasting but struggle with long-range dependencies due to the vanishing gradient problem.

### Transformers

The Transformer architecture, introduced in the paper "Attention Is All You Need" (Vaswani et al., 2017), replaced recurrence with self-attention. Each token in a sequence attends to every other token, capturing long-range dependencies efficiently. Transformers are the basis of large language models such as GPT and BERT.

## Regularization

Overfitting occurs when a network memorizes training data instead of learning general patterns. Common regularization techniques include:

- **Dropout**: randomly zeroing a fraction of neuron outputs during training, forcing the network to learn redundant representations.
- **Weight Decay (L2 regularization)**: adds a penalty proportional to the square of each weight to the loss, discouraging large weights.
- **Batch Normalization**: normalizes activations within a mini-batch, stabilizing training and allowing higher learning rates.
- **Early Stopping**: halt training when validation loss stops improving.

## Optimization Algorithms

Beyond vanilla SGD, modern optimizers adapt the learning rate per parameter:

- **Momentum**: accumulates a velocity vector in directions of persistent gradient, dampening oscillations.
- **Adam**: combines momentum with per-parameter adaptive learning rates based on first and second moment estimates. The default choice for most deep learning tasks.
- **AdaGrad**: accumulates squared gradients, giving frequently updated parameters smaller updates.

## Applications

Neural networks are behind many technologies in daily life:

- **Computer Vision**: image classification (ResNet, EfficientNet), object detection (YOLO), image generation (GANs, Diffusion Models).
- **Natural Language Processing**: machine translation, sentiment analysis, question answering, and large language models.
- **Reinforcement Learning**: combined with neural networks in Deep Q-Networks (DQN) and Proximal Policy Optimization (PPO) to learn strategies for games and robotics.
- **Scientific Discovery**: protein structure prediction (AlphaFold), drug discovery, climate modeling.

## Historical Milestones

- **1958**: Frank Rosenblatt introduces the Perceptron, the first trainable single-layer network.
- **1986**: Rumelhart, Hinton, and Williams popularize backpropagation for multi-layer networks.
- **1998**: Yann LeCun demonstrates convolutional networks on handwritten digit recognition (LeNet).
- **2012**: AlexNet wins ImageNet by a large margin, triggering the deep learning renaissance.
- **2017**: The Transformer architecture is introduced, revolutionizing NLP.
- **2020–present**: Large language models (GPT-3, GPT-4, Claude) demonstrate emergent capabilities at scale.
