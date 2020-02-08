import numpy as np
import torch
from rlpyt.utils.tensor import infer_leading_dims, restore_leading_dims
from rlpyt.models.mlp import MlpModel
from rlpyt.models.conv2d import Conv2dModel
from torch.nn.functional import relu
from torch.nn import Linear


class PiMCPModel(torch.nn.Module):

    def __init__(
            self,
            observation_shape,
            hidden_sizes,
            action_size,
            num_primitives=4
    ):
        super().__init__()
        assert hasattr(observation_shape, 'state'), "mcp model requires observation dict to contain state attribute"
        assert hasattr(observation_shape, 'goal'), "mcp model requires observation to contain goal attribute"
        self.num_primitives = num_primitives
        self.action_size = action_size
        self.primitives_l1 = Linear(observation_shape.state[0], 512)
        self.primitives_l2 = Linear(512, 256)
        self.primitives_l3s = []
        self.primitives_l4s = []
        for i in range(num_primitives):
            self.primitives_l3s.append(Linear(256, 256))
            # action size x2 because of mean standard deviation for each action
            self.primitives_l4s.append(Linear(256, action_size * 2))

        self.gating_state_l1 = Linear(observation_shape.state[0], 512)
        self.gating_state_l2 = Linear(512, 256)
        self.gating_goal_l1 = Linear(observation_shape.goal[0], 512)
        self.gating_goal_l2 = Linear(512, 256)
        self.gating_l3 = Linear(512, 256)
        self.gating_l4 = Linear(256, num_primitives)
        init_log_std = 0.
        self.log_std = torch.nn.Parameter(init_log_std * torch.ones(action_size))

    def forward(self, observation, prev_action, prev_reward):
        """Feedforward layers process as [T*B,H]. Return same leading dims as
        input, can be [T,B], [B], or []."""

        # Infer (presence of) leading dimensions: [T,B], [B], or [].
        lead_dim, T, B, _ = infer_leading_dims(observation.state, 1)
        goal_input = observation.goal.view(T * B, -1)
        state_input = observation.state.view(T * B, -1)
        # inputs now with just one batch dimension at front
        gating_state = relu(self.gating_state_l1(state_input))
        gating_state = relu(self.gating_state_l2(gating_state))
        gating_goal = relu(self.gating_goal_l1(goal_input))
        gating_goal = relu(self.gating_goal_l2(gating_goal))
        gating = relu(self.gating_l3(torch.cat((gating_state, gating_goal), -1)))
        gating = self.gating_l4(gating)

        primitives = relu(self.primitives_l1(state_input))
        primitives = self.primitives_l2(primitives)

        primitives_means = []
        primitves_log_stds = []
        for i in range(self.num_primitives):
            x = self.primitives_l3s[i](primitives)
            x = self.primitives_l4s[i](x)
            primitives_means.append(x[:,:self.action_size])
            primitves_log_stds.append(x[:,self.action_size:])

        log_std = torch.zeros((T*B, self.action_size,))
        mu = torch.zeros((T*B, self.action_size))
        gating = gating.reshape((T*B, self.num_primitives, 1)).expand(-1, -1, self.action_size)
        for i in range(self.num_primitives):
            x = torch.div(gating[:,i].expand((T*B, self.action_size)), primitves_log_stds[i])
            log_std = torch.add(log_std, x)
            mu = torch.add(mu, torch.mul(x, primitives_means[i]))
        log_std = torch.div(1, log_std)
        mu = torch.mul(mu, log_std)

        # Restore leading dimensions: [T,B], [B], or [], as input.
        mu, log_std = restore_leading_dims((mu, log_std), lead_dim, T, B)
        return mu, log_std


class QofMCPModel(torch.nn.Module):

    def __init__(
            self,
            observation_shape,
            hidden_sizes,
            action_size,
    ):
        super().__init__()
        assert hasattr(observation_shape, 'state'), "mcp model requires observation dict to contain state attribute"
        assert hasattr(observation_shape, 'goal'), "mcp model requires observation to contain goal attribute"
        self.mlp = MlpModel(
            input_size=observation_shape.state[0] + observation_shape.goal[0] + action_size,
            hidden_sizes=[512, 256, 128],
            output_size=1
        )

    def forward(self, observation, prev_action, prev_reward, action):
        """Feedforward layers process as [T*B,H]. Return same leading dims as
        input, can be [T,B], [B], or []."""

        # Infer (presence of) leading dimensions: [T,B], [B], or [].
        lead_dim, T, B, _ = infer_leading_dims(observation.state, 1)
        goal = observation.goal.view(T * B, -1)
        state = observation.state.view(T * B, -1)
        action = action.view(T * B, -1)
        q_input = torch.cat([state, goal, action], dim=1)
        q = self.mlp(q_input).squeeze(-1)
        q = restore_leading_dims(q, lead_dim, T, B)
        return q