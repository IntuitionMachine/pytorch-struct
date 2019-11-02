import torch
import torch.distributions
import numpy as np
from pytorch_memlab import MemReporter
from .sparse import *

class Semiring:
    """
    Base semiring class.

    Based on description in:

    * Semiring parsing :cite:`goodman1999semiring`

    """

    @classmethod
    def size(cls):
        "Additional *ssize* first dimension needed."
        return 1

    @classmethod
    def dot(cls, *ls):
        "Dot product along last dim."
        return cls.sum(cls.times(*ls))
    
    @classmethod
    def banded_dot(cls, a, b, band, offset_a, offset_b):
        return sparse_banded_combine(a, b, band, offset_a, offset_b,
                                     semiring=cls,fn=cls.dot)

    
    @classmethod
    def times(cls, *ls):
        "Multiply a list of tensors together"
        cur = ls[0]
        for l in ls[1:]:
            cur = cls.mul(cur, l)
        return cur

    @classmethod
    def convert(cls, potentials):
        "Convert to semiring by adding an extra first dimension."
        return potentials.unsqueeze(0)

    @classmethod
    def unconvert(cls, potentials):
        "Unconvert from semiring by removing extra first dimension."
        return potentials.squeeze(0)

    @staticmethod
    def zero_(xs):
        "Fill *ssize x ...* tensor with additive identity."
        raise NotImplementedError()

    @staticmethod
    def one_(xs):
        "Fill *ssize x ...* tensor with multiplicative identity."
        raise NotImplementedError()

    @staticmethod
    def sum(xs, dim=-1):
        "Sum over *dim* of tensor."
        raise NotImplementedError()

    @classmethod
    def plus(cls, a, b):
        return cls.sum(torch.stack([a, b], dim=-1))

    dg = False


class _Base(Semiring):
    @staticmethod
    def mul(a, b):
        return torch.mul(a, b)

    @staticmethod
    def prod(a, dim=-1):
        return torch.prod(a, dim=dim)

    @staticmethod
    def zero_(xs):
        return xs.fill_(0)

    @staticmethod
    def one_(xs):
        return xs.fill_(1)


class _BaseLog(Semiring):
    @staticmethod
    def mul(a, b):
        return a + b

    @staticmethod
    def zero_(xs):
        return xs.fill_(-1e5)

    @staticmethod
    def one_(xs):
        return xs.fill_(0.0)

    @staticmethod
    def prod(a, dim=-1):
        return torch.sum(a, dim=dim)


class StdSemiring(_Base):
    """
    Implements the counting semiring (+, *, 0, 1).

    """

    @staticmethod
    def sum(xs, dim=-1):
        return torch.sum(xs, dim=dim)


class LogSemiring(_BaseLog):
    """
    Implements the log-space semiring (logsumexp, +, -inf, 0).

    Gradients give marginals.
    """

    dg = True

    @staticmethod
    def sum(xs, dim=-1):
        return torch.logsumexp(xs, dim=dim)

    @classmethod
    def dot_grad(cls, a, b):
        "Dot product along last dim."
        c = a + b
        part = torch.logsumexp(c, dim=-1)
        return part, (c - part.unsqueeze(-1)).exp()


def back(x):
    x.backward()
    
def unaccumulate_(a, b, grad_output, fn, step=10000):
    slices = []
    a_grad = a.clone().fill_(0)    
    b_grad = b.clone().fill_(0)
    # print("chcek", a_grad.shape)    
    # a_grad2 = torch.tensor(0.0, device=a.device, dtype=a.dtype).set_(a.clone().storage(), a.storage_offset(), a.size(), a.stride()).fill_(0)
    # b_grad2 = torch.tensor(0.0, device=b.device, dtype=b.dtype).set_(b.clone().storage(), b.storage_offset(), b.size(), b.stride()).fill_(0)

    total = 1
    for s in grad_output.shape:
        slices.append(slice(s))
        total *= s
        
    a_one = []
    for i, v in enumerate(a.shape[:-1]):
        if v == 1:
            a_one.append(i)
    b_one = []
    for i, v in enumerate(b.shape[:-1]):
        if v == 1:
            b_one.append(i)
            
    indices = torch.tensor(np.mgrid[slices]).view(len(grad_output.shape), -1)
    
    for p in range(0, total, step):
        ind = indices[:, p : p + step].unbind()

        a_ind = list(ind)
        for v in a_one:
            a_ind[v] = a_ind[v].clone().fill_(0).long()
        b_ind = list(ind)
        for v in b_one:
            b_ind[v] = b_ind[v].clone().fill_(0).long()
            
        q = fn(a[tuple(a_ind)], b[tuple(b_ind)], grad_output[tuple(ind)])
        # a_grad[tuple(a_ind)] = a_grad[tuple(a_ind)] + q
        a_grad.index_put_(tuple(a_ind),  q, accumulate=True)
        b_grad.index_put_(tuple(b_ind),  q, accumulate=True)
        # a_grad2.index_put_(tuple(a_ind),  q, accumulate=True)
        # b_grad2.index_put_(tuple(b_ind),  q, accumulate=True)
        # assert torch.isclose(a_grad, a_grad2).all(), a_grad - a_grad2
        
    return a_grad, b_grad


def accumulate_(a, b, ret, fn, step=10000):
    slices = []
    total = 1
    for s in ret.shape:
        slices.append(slice(s))
        total *= s
    
    a_one = []
    for i, v in enumerate(a.shape[:-1]):
        if v == 1:
            a_one.append(i)
    b_one = []
    for i, v in enumerate(b.shape[:-1]):
        if v == 1:
            b_one.append(i)
    indices = torch.tensor(np.mgrid[slices]).view(len(ret.shape), -1)
    for p in range(0, total, step):
        
        ind = indices[:, p : p + step].unbind()
        if ind[0].shape[0] == 0:
            continue
        a_ind = list(ind)
        for v in a_one:
            a_ind[v] = a_ind[v].clone().fill_(0)
        b_ind = list(ind)
        for v in b_one:
            b_ind[v] = b_ind[v].clone().fill_(0)
        ret[ind] = fn(a[tuple(a_ind)], b[tuple(b_ind)])


# def unaccumulate_(a, b, ret, grad_output, fn, step=1000):
#     slices = []
#     total = 1
#     a = a.clone().requires_grad_(True)
#     b = b.clone().requires_grad_(True)
#     for s in ret.shape:
#         slices.append(slice(s))
#         total *= s
#     a_one = []
#     for i, v in enumerate(a.shape):
#         if v == 1:
#             a_one.append(i)
#     b_one = []
#     for i, v in enumerate(b.shape):
#         if v == 1:
#             b_one.append(i)
#     indices = torch.tensor(np.mgrid[slices]).view(len(ret.shape), -1)
#     for p in range(0, total, step):
#         ind = indices[:, p:p+step].unbind()
#         a_ind = list(ind)
#         for v in a_one:
#             a_ind[v] = a_ind[v].clone().fill_(0)
#         b_ind = list(ind)
#         for v in b_one:
#             b_ind[v] = b_ind[v].clone().fill_(0)
#         ret[ind] = fn(a[tuple(a_ind)], b[tuple(b_ind)])
#         print(ret[ind])
#         torch.autograd.grad(ret[ind], (a, b), grad_output[ind])
#     return a.grad, b.grad


        # torch.grad(grad_output)
        # batch = a.shape[1]
        # for p in range(0, batch, 10):
        # back = torch.softmax(a + b, dim=-1) \
        #             .mul(grad_output.unsqueeze(-1))
        # grad_a = back.sum(dim=asum, keepdim=True)
        # grad_b = back.sum(dim=bsum, keepdim=True)
        

def LogMemSemiring(max_size=100000):
    store = []
    class _LogMemDot(torch.autograd.Function):
        @staticmethod
        def forward(ctx, a, b):
            ctx.save_for_backward(a, b)

            
            store.append(a)
            store.append(b)
            st = []
            batch = a.shape[1]
            size = [max(p, q) for p, q in zip(a.shape, b.shape)][:-1]
            # return torch.logsumexp(a + b, dim=-1)

            ret = torch.zeros(*size, dtype=a.dtype, device=a.device)
            accumulate_(a, b, ret, lambda a, b: torch.logsumexp(a + b, dim=-1), step=max_size // a.shape[-1] + 2)
            return ret

        @staticmethod
        def backward(ctx, grad_output):
            a, b = ctx.saved_tensors
            print("backing out", a.shape)
            reporter = MemReporter()
            reporter.report()

            size = [max(p, q) for p, q in zip(a.shape, b.shape)][:-1]

            fn = lambda a, b, g: torch.softmax(a + b, dim=-1).mul(g.unsqueeze(-1))
            if True:
                grad_a, grad_b = unaccumulate_(
                    a, b, grad_output, fn,
                    step=max_size // a.shape[-1] + 2 
                )
            else:
                asum, bsum = [], []
                for i, (x, y) in enumerate(zip(a.shape, b.shape)):
                    if x == 1:
                        asum.append(i)
                    if y == 1:
                        bsum.append(i)
                back = fn(a, b, grad_output)
                grad_a = back.sum(dim=asum, keepdim=True)
                grad_b = back.sum(dim=bsum, keepdim=True)
                
            print("backing out 2",
                  grad_a.shape, grad_b.shape, a.shape)
            reporter = MemReporter()
            reporter.report()
                
            return grad_a, grad_b

    class _LogMemBandedDot(torch.autograd.Function):
        @staticmethod
        def forward(ctx, a, b, band, o1, o2):
            ctx.save_for_backward(a, b, torch.tensor([band, o1, o2]))
            
            store.append(a)
            store.append(b)
            return sparse_banded_combine(a, b, band, o1, o2,
                                         semiring=LogSemiring,
                                         fn=lambda a, b: torch.logsumexp(a + b, dim=-1))

            # return torch.logsumexp(a + b, dim=-1)
            # st = []
            # batch = a.shape[1]
            # size = [max(p, q) for p, q in zip(a.shape, b.shape)][:-1]
            # ret = torch.zeros(*size, dtype=a.dtype, device=a.device)
            # accumulate_(a, b, ret, lambda a, b: torch.logsumexp(a + b, dim=-1),
            #             step=max_size // a.shape[-1] + 2)
            # return ret

        @staticmethod
        def backward(ctx, grad_output):
            a, b, opt = ctx.saved_tensors
            band, o1, o2 = opt.tolist()
            print("backing out", a.shape)
            reporter = MemReporter()
            reporter.report()

            size = [max(p, q) for p, q in zip(a.shape, b.shape)][:-1]
            def fn(a, b, g):
                def inner(a, b):
                    # print("inner", a.shape, b.shape)
                    return torch.softmax(a+b, -1).transpose(-1, -3).transpose(-1, -2)
                p = sparse_banded_combine(a, b, band, o1, o2,
                                          semiring=LogSemiring,
                                          fn=inner)
                p = p.transpose(-1, -3).transpose(-2, -3)
                return p.mul(g.unsqueeze(-1)).sum(-1)
            
            if True:
                asum, bsum = [], []
                for i, (x, y) in enumerate(zip(a.shape, b.shape)):
                    if x == 1:
                        asum.append(i)
                    if y == 1:
                        bsum.append(i)
                back = fn(a, b, grad_output)
                grad_a = back.sum(dim=asum, keepdim=True)
                grad_b = back.sum(dim=bsum, keepdim=True)
                
            print("backing out 2",
                  grad_a.shape, grad_b.shape, a.shape)
            reporter = MemReporter()
            reporter.report()
                
            return grad_a, grad_b, None, None, None



    class _LogMemSemiring(_BaseLog):
        """
        Implements the log-space semiring (logsumexp, +, -inf, 0).

        Gradients give marginals.
        """

        @staticmethod
        def sum(xs, dim=-1):
            return torch.logsumexp(xs, dim=dim)

        @classmethod
        def dot(cls, a, b):
            "Dot product along last dim."
            return _LogMemDot.apply(a, b, )
            # return cls.sum(cls.times(*ls))

        @classmethod
        def banded_dot(cls, a, b, band, offset_a, offset_b):
            return _LogMemBandedDot.apply(a, b, band, offset_a, offset_b)
        
            
        @classmethod
        def dot_grad(cls, a, b):
            "Dot product along last dim."
            c = a + b
            part = torch.logsumexp(c, dim=-1)
            return part, (c - part.unsqueeze(-1)).exp()
    return _LogMemSemiring

class MaxSemiring(_BaseLog):
    """
    Implements the max semiring (max, +, -inf, 0).

    Gradients give argmax.
    """

    dg = True

    @staticmethod
    def sum(xs, dim=-1):
        return torch.max(xs, dim=dim)[0]

    @classmethod
    def dot_grad(cls, a, b):
        "Dot product along last dim."
        c = a + b
        part, argmax = torch.max(c, dim=-1)
        return part, torch.nn.functional.one_hot(argmax, a.shape[-1])

    @staticmethod
    def sparse_sum(xs, dim=-1):
        m, a = torch.max(xs, dim=dim)
        return m, (torch.zeros(a.shape).long(), a)

def TempMax(alpha):
    pass
    
def KMaxSemiring(k):
    """
    Implements the k-max semiring (kmax, +, [-inf, -inf..], [0, -inf, ...]).

    Gradients give k-argmax.
    """

    class KMaxSemiring(_BaseLog):
        @staticmethod
        def size():
            return k

        @classmethod
        def convert(cls, orig_potentials):
            potentials = torch.zeros(
                (k,) + orig_potentials.shape,
                dtype=orig_potentials.dtype,
                device=orig_potentials.device,
            )
            cls.zero_(potentials)
            potentials[0] = orig_potentials
            return potentials

        @classmethod
        def one_(cls, xs):
            cls.zero_(xs)
            xs[0].fill_(0)
            return xs

        @staticmethod
        def unconvert(potentials):
            return potentials[0]

        @staticmethod
        def sum(xs, dim=-1):
            if dim == -1:
                xs = xs.permute(tuple(range(1, xs.dim())) + (0,))
                xs = xs.contiguous().view(xs.shape[:-2] + (-1,))
                xs = torch.topk(xs, k, dim=-1)[0]
                xs = xs.permute((xs.dim() - 1,) + tuple(range(0, xs.dim() - 1)))
                assert xs.shape[0] == k
                return xs
            assert False

        @staticmethod
        def sparse_sum(xs, dim=-1):
            if dim == -1:
                xs = xs.permute(tuple(range(1, xs.dim())) + (0,))
                xs = xs.contiguous().view(xs.shape[:-2] + (-1,))
                xs, xs2 = torch.topk(xs, k, dim=-1)
                xs = xs.permute((xs.dim() - 1,) + tuple(range(0, xs.dim() - 1)))
                xs2 = xs2.permute((xs.dim() - 1,) + tuple(range(0, xs.dim() - 1)))
                assert xs.shape[0] == k
                return xs, (xs2 % k, xs2 // k)
            assert False

        @staticmethod
        def mul(a, b):
            a = a.view((k, 1) + a.shape[1:])
            b = b.view((1, k) + b.shape[1:])
            c = a + b
            c = c.contiguous().view((k * k,) + c.shape[2:])
            ret = torch.topk(c, k, 0)[0]
            assert ret.shape[0] == k
            return ret

    return KMaxSemiring


class _SampledLogSumExp(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input, dim):
        ctx.save_for_backward(input, torch.tensor(dim))
        return torch.logsumexp(input, dim=dim)

    @staticmethod
    def backward(ctx, grad_output):
        logits, dim = ctx.saved_tensors
        grad_input = None
        if ctx.needs_input_grad[0]:

            def sample(ls):
                pre_shape = ls.shape
                draws = torch.multinomial(
                    ls.softmax(-1).view(-1, pre_shape[-1]), 1, True
                )
                draws.squeeze(1)
                return (
                    torch.nn.functional.one_hot(draws, pre_shape[-1])
                    .view(*pre_shape)
                    .type_as(ls)
                )

            if dim == -1:
                s = sample(logits)
            else:
                dim = dim if dim >= 0 else logits.dim() + dim
                perm = [i for i in range(logits.dim()) if i != dim] + [dim]
                rev_perm = [a for a, b in sorted(enumerate(perm), key=lambda a: a[1])]
                s = sample(logits.permute(perm)).permute(rev_perm)

            grad_input = grad_output.unsqueeze(dim).mul(s)
        return grad_input, None


class SampledSemiring(_BaseLog):
    """
    Implements a sampling semiring (logsumexp, +, -inf, 0).

    "Gradients" give sample.

    This is an exact forward-filtering, backward-sampling approach.
    """

    @staticmethod
    def sum(xs, dim=-1):
        return _SampledLogSumExp.apply(xs, dim)


bits = torch.tensor([pow(2, i) for i in range(1, 18)])


class _MultiSampledLogSumExp(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input, dim):
        part = torch.logsumexp(input, dim=dim)
        ctx.save_for_backward(input, part, torch.tensor(dim))
        return part

    @staticmethod
    def backward(ctx, grad_output):

        logits, part, dim = ctx.saved_tensors
        grad_input = None
        if ctx.needs_input_grad[0]:

            def sample(ls):
                pre_shape = ls.shape
                draws = torch.multinomial(
                    ls.softmax(-1).view(-1, pre_shape[-1]), 16, True
                )
                draws = draws.transpose(0, 1)
                return (
                    torch.nn.functional.one_hot(draws, pre_shape[-1])
                    .view(16, *pre_shape)
                    .type_as(ls)
                )

            if dim == -1:
                s = sample(logits)
            else:
                dim = dim if dim >= 0 else logits.dim() + dim
                perm = [i for i in range(logits.dim()) if i != dim] + [dim]
                rev_perm = [0] + [
                    a + 1 for a, b in sorted(enumerate(perm), key=lambda a: a[1])
                ]
                s = sample(logits.permute(perm)).permute(rev_perm)

            dim = dim if dim >= 0 else logits.dim() + dim
            final = (grad_output % 2).unsqueeze(0)
            mbits = bits[:].type_as(grad_output)
            on = grad_output.unsqueeze(0) % mbits.view(17, *[1] * grad_output.dim())
            on = on[1:] - on[:-1]
            old_bits = (on + final == 0).unsqueeze(dim + 1)

            grad_input = (
                mbits[:-1]
                .view(16, *[1] * (s.dim() - 1))
                .mul(s.masked_fill_(old_bits, 0))
            )

        return torch.sum(grad_input, dim=0), None


class MultiSampledSemiring(_BaseLog):
    """
    Implements a multi-sampling semiring (logsumexp, +, -inf, 0).

    "Gradients" give up to 16 samples with replacement.
    """

    @staticmethod
    def sum(xs, dim=-1):
        return _MultiSampledLogSumExp.apply(xs, dim)

    @staticmethod
    def to_discrete(xs, j):
        i = j
        final = xs % 2
        mbits = bits.type_as(xs)
        return (((xs % mbits[i + 1]) - (xs % mbits[i]) + final) != 0).type_as(xs)


class EntropySemiring(Semiring):
    """
    Implements an entropy expectation semiring.

    Computes both the log-values and the running distributional entropy.

    Based on descriptions in:

    * Parameter estimation for probabilistic finite-state transducers :cite:`eisner2002parameter`
    * First-and second-order expectation semirings with applications to minimum-risk training on translation forests :cite:`li2009first`
    """

    @staticmethod
    def size():
        return 2

    @staticmethod
    def convert(xs):
        values = torch.zeros((2,) + xs.shape).type_as(xs)
        values[0] = xs
        values[1] = 0
        return values

    @staticmethod
    def unconvert(xs):
        return xs[1]

    @staticmethod
    def sum(xs, dim=-1):
        assert dim != 0
        d = dim - 1 if dim > 0 else dim
        part = torch.logsumexp(xs[0], dim=d)
        log_sm = xs[0] - part.unsqueeze(d)
        sm = log_sm.exp()
        return torch.stack((part, torch.sum(xs[1].mul(sm) - log_sm.mul(sm), dim=d)))

    @staticmethod
    def mul(a, b):
        return torch.stack((a[0] + b[0], a[1] + b[1]))

    @classmethod
    def prod(cls, xs, dim=-1):
        return xs.sum(dim)

    @staticmethod
    def zero_(xs):
        xs[0].fill_(-1e5)
        xs[1].fill_(0)
        return xs

    @staticmethod
    def one_(xs):
        xs[0].fill_(0)
        xs[1].fill_(0)
        return xs


class SparseMaxSemiring(_BaseLog):
    """

    Implements differentiable dynamic programming with a sparsemax semiring (sparsemax, +, -inf, 0).

    Sparse-max gradients give a more sparse set of marginal like terms.

    * From softmax to sparsemax- A sparse model of attention and multi-label classification :cite:`martins2016softmax`
    * Differentiable dynamic programming for structured prediction and attention :cite:`mensch2018differentiable`
    """

    @staticmethod
    def sum(xs, dim=-1):
        return _SimplexProject.apply(xs, dim)


class _SimplexProject(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input, dim, z=1):
        w_star = project_simplex(input, dim)
        ctx.save_for_backward(input, w_star.clone(), torch.tensor(dim))
        x = input.mul(w_star).sum(dim) - w_star.norm(p=2, dim=dim)
        return x

    @staticmethod
    def backward(ctx, grad_output):
        input, w_star, dim = ctx.saved_tensors
        w_star.requires_grad_(True)

        grad_input = None
        if ctx.needs_input_grad[0]:
            wstar = _SparseMaxGrad.apply(w_star, dim)
            grad_input = grad_output.unsqueeze(dim).mul(wstar)
        return grad_input, None, None


class _SparseMaxGrad(torch.autograd.Function):
    @staticmethod
    def forward(ctx, w_star, dim):
        ctx.save_for_backward(w_star, dim)
        return w_star

    @staticmethod
    def backward(ctx, grad_output):
        w_star, dim = ctx.saved_tensors
        return sparsemax_grad(grad_output, w_star, dim.item()), None


def project_simplex(v, dim, z=1):
    v_sorted, _ = torch.sort(v, dim=dim, descending=True)
    cssv = torch.cumsum(v_sorted, dim=dim) - z
    ind = torch.arange(1, 1 + v.shape[dim]).to(dtype=v.dtype)
    cond = v_sorted - cssv / ind >= 0
    k = cond.sum(dim=dim, keepdim=True)
    tau = cssv.gather(dim, k - 1) / k.to(dtype=v.dtype)
    w = torch.clamp(v - tau, min=0)
    return w


def sparsemax_grad(dout, w_star, dim):
    out = dout.clone()
    supp = w_star > 0
    out[w_star <= 0] = 0
    nnz = supp.to(dtype=dout.dtype).sum(dim=dim, keepdim=True)
    out = out - (out.sum(dim=dim, keepdim=True) / nnz)
    out[w_star <= 0] = 0
    return out
