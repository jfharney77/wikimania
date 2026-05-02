# Reinforcement Learning

## Overview

Reinforcement learning (RL) is a branch of machine learning in which an agent learns to make decisions by interacting with an environment. Unlike supervised learning, there is no labeled dataset — the agent discovers what to do by trial and error, receiving numerical rewards or penalties as feedback. The goal is to find a policy, a mapping from states to actions, that maximizes cumulative reward over time.

RL has produced some of the most dramatic demonstrations of machine intelligence, including defeating world champions at chess, Go, and StarCraft II, and training robotic hands to solve Rubik's cubes.

## Core Concepts

### Agent and Environment

The agent is the decision-maker. The environment is everything the agent interacts with. At each timestep the agent observes the current state, selects an action, and receives a reward signal along with the next state. This loop continues until a terminal condition is reached (end of episode) or indefinitely in continuous tasks.

### State, Action, and Reward

- **State (s)**: a representation of the environment at a given timestep — sensor readings, game board position, pixel frames, etc.
- **Action (a)**: a choice the agent can make — move left, apply torque, place a piece.
- **Reward (r)**: a scalar signal indicating how good or bad the action was. The agent does not receive explicit instructions; it must infer good behavior from the reward signal alone.

### Policy

A policy π defines the agent's behavior: given a state, it outputs a probability distribution over actions (stochastic policy) or a single action (deterministic policy). Learning the optimal policy is the central objective of RL.

### Value Functions

Value functions estimate the long-term desirability of states or state-action pairs:

- **State-value function V(s)**: expected cumulative reward starting from state s and following policy π.
- **Action-value function Q(s, a)**: expected cumulative reward starting from state s, taking action a, and then following policy π. Also called the Q-function.

### Discount Factor

Future rewards are typically discounted by a factor γ (gamma, between 0 and 1). A reward received k steps in the future contributes γᵏ times its value to the current estimate. High γ makes the agent far-sighted; low γ makes it myopic.

## The Exploration-Exploitation Dilemma

An RL agent must balance:
- **Exploitation**: choosing the action it currently believes is best.
- **Exploration**: trying less-familiar actions that might yield higher reward.

Too much exploitation leads to getting stuck in suboptimal behavior. Too much exploration wastes time on poor actions. Common strategies include ε-greedy (take a random action with probability ε), softmax action selection, and Upper Confidence Bound (UCB).

## Key Algorithms

### Q-Learning

Q-learning is a model-free, off-policy algorithm that learns the Q-function directly from experience. The update rule is:

```
Q(s, a) ← Q(s, a) + α [r + γ · max Q(s', a') − Q(s, a)]
```

where α is the learning rate and s' is the next state. Given enough exploration, Q-learning converges to the optimal Q-function.

### SARSA

SARSA (State-Action-Reward-State-Action) is an on-policy variant of Q-learning. It updates Q using the action actually taken in the next state rather than the maximum:

```
Q(s, a) ← Q(s, a) + α [r + γ · Q(s', a') − Q(s, a)]
```

### Deep Q-Network (DQN)

DQN, introduced by DeepMind in 2013, combines Q-learning with deep neural networks. A convolutional neural network takes raw pixels as input and outputs Q-values for all actions. Two key innovations made training stable:

- **Experience replay**: transitions are stored in a replay buffer and sampled randomly, breaking temporal correlations.
- **Target network**: a periodically updated copy of the network is used to compute target Q-values, reducing oscillations.

DQN achieved human-level performance on 49 Atari games from raw pixels alone.

### Policy Gradient Methods

Rather than learning a value function and deriving a policy from it, policy gradient methods directly optimize the policy parameters θ by gradient ascent on expected reward. The REINFORCE algorithm computes:

```
∇θ J(θ) = E[∇θ log π(a|s) · G]
```

where G is the return (cumulative discounted reward). Policy gradients work naturally with continuous action spaces.

### Actor-Critic Methods

Actor-critic algorithms combine the strengths of value-based and policy gradient approaches:
- The **actor** is the policy — it selects actions.
- The **critic** is a value function — it evaluates how good the actor's choices are and provides a lower-variance gradient signal.

### Proximal Policy Optimization (PPO)

PPO, developed at OpenAI, is currently one of the most widely used RL algorithms. It updates the policy in small, controlled steps using a clipped surrogate objective, preventing catastrophically large policy updates. PPO is the algorithm behind OpenAI Five (Dota 2) and many robotics applications.

### Soft Actor-Critic (SAC)

SAC adds an entropy regularization term to the reward, encouraging the agent to act as randomly as possible while still maximizing reward. This promotes exploration and leads to robust, diverse policies. SAC is particularly effective in continuous control tasks.

## Model-Based vs Model-Free

- **Model-free** methods (DQN, PPO, SAC) learn directly from experience without building an explicit model of the environment. They are simpler but sample-inefficient.
- **Model-based** methods learn a world model — a predictor of next states and rewards — and use it to plan or generate synthetic experience. They require fewer real interactions but are harder to train correctly.

AlphaZero and MuZero are prominent model-based algorithms that learn the world model and use Monte Carlo Tree Search (MCTS) for planning.

## Landmark Results

- **TD-Gammon** (1992): RL agent trained by self-play reached expert-level backgammon.
- **DQN on Atari** (2013): DeepMind demonstrated human-level play on dozens of games from raw pixels.
- **AlphaGo** (2016): defeated world champion Lee Sedol at Go using deep RL and MCTS.
- **AlphaZero** (2017): mastered chess, shogi, and Go from scratch via self-play in hours.
- **OpenAI Five** (2019): defeated world champions at Dota 2, a real-time strategy game with long-horizon decisions.
- **AlphaStar** (2019): reached Grandmaster level at StarCraft II.
- **ChatGPT / RLHF** (2022–present): reinforcement learning from human feedback (RLHF) aligns large language models with human preferences.

## Applications

- **Robotics**: locomotion, manipulation, sim-to-real transfer.
- **Game playing**: board games, video games, real-time strategy.
- **Autonomous driving**: lane keeping, decision-making at intersections.
- **Recommendation systems**: personalizing content feeds.
- **Drug discovery**: optimizing molecular structures for target binding.
- **Data center cooling**: Google used RL to reduce cooling energy by 40%.
- **Large language models**: RLHF fine-tunes models to follow instructions and avoid harmful outputs.

## Challenges

- **Sample inefficiency**: model-free RL may require millions of environment interactions to learn simple tasks.
- **Reward hacking**: agents find unexpected ways to maximize reward that violate designer intent.
- **Sparse rewards**: in many real tasks, reward signals are rare, making credit assignment difficult.
- **Sim-to-real gap**: policies trained in simulation often fail when transferred to physical hardware.
- **Safety**: ensuring agents behave safely during exploration is critical for real-world deployment.
