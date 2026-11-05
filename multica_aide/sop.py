"""Small state/option model used by the standalone wrapper."""
import argparse
import re


class Parameter:
    def __init__(self, flag, prompt='', *, required=True, value=None, **kwargs):
        self.flag, self.prompt = flag, prompt
        self.required, self.value, self.kwargs = required, value, kwargs

    def add_to(self, parser):
        parser.add_argument(self.flag, help=self.prompt, **self.kwargs)

    def words(self):
        if self.value is not None:
            return [self.flag, str(self.value)]
        if not self.required:
            return []
        placeholder = '<' + self.flag.lstrip('-').replace('-', '_') + '>'
        return [self.flag, placeholder] if self.flag.startswith('-') else ['--', placeholder]


class Option:
    def __init__(self, name, target, prompt, action=None, *, params=()):
        self.name, self.target, self.prompt = name, target, prompt
        self.action, self.params = action, tuple(params)

    def callback(self):
        return [self.name, *(word for param in self.params for word in param.words())]


class State:
    def __init__(self, name, description, *, initial=False, final=False,
                 options=(), precheck=None, view=None):
        self.name, self.description = name, description
        self.initial, self.final = initial, final
        self.options, self.precheck, self.view = options, precheck, view

    def choices(self, context):
        return tuple(self.options(context) if callable(self.options) else self.options)


class Completed(Exception):
    """A read-only precheck found that this issue has already completed the SOP."""
    def __init__(self, instruction, data=None, *, target='finished'):
        self.result = {'phase': target, 'instruction': instruction, 'data': data or {}}


class Workflow:
    def __init__(self, *states, params=()):
        self.states = {state.name: state for state in states}
        self.params = tuple(params)
        initial = [state for state in states if state.initial]
        final = [state for state in states if state.final]
        if (len(self.states) != len(states) or len(initial) != 1 or not final or initial[0].final):
            raise ValueError('Declare unique states, one initial state and at least one final state')
        self.initial = initial[0].name
        for state in states:
            if not re.fullmatch(r'[a-z][a-z0-9_]*', state.name) or state.name in {'init', 'repaired', 'error', 'blocked'}:
                raise ValueError('Invalid or reserved state name: ' + state.name)
            if state.final and (callable(state.options) or state.options):
                raise ValueError('Final states cannot have options')
            if not callable(state.options):
                self.validate_options(state.choices({}))

    def validate_options(self, options):
        for option in options:
            if option.target not in self.states or not re.fullmatch(r'[a-z][a-z0-9_-]*', option.name) or option.name in {'init', 'repaired', 'quit'}:
                raise ValueError('Option needs a command name and a declared target: ' + option.name)

    def configure(self, parser):
        for param in self.params:
            param.add_to(parser)

    def before(self, context):
        state = self.states[context['phase']]
        context['initial'] = state.precheck(context) if state.precheck else None
        return context['initial']

    def after(self, context, args, initial, result):
        context.update(args=args, initial=initial, result=result)
        state = self.states[result['phase']]
        if state.precheck:
            state.precheck(context)

    def guidance(self, state, context, result=None):
        context['phase'] = state.name
        result = dict(result or {})
        view = state.view(context) if state.view else {}
        options = state.choices(context)
        self.validate_options(options)
        text = result.pop('instruction', None) or view.get('instruction') or state.description
        result.setdefault('data', view.get('data'))
        return {**result, 'phase': state.name, 'instruction': text,
                'callback': options[0].callback() if options else None,
                'callback_instruction': options[0].prompt if options else None,
                'alternatives': [{'instruction': option.prompt, 'callback': option.callback()}
                                 for option in options[1:]]}

    def step(self, args, context, phase):
        state = self.states[phase]
        context.update(phase=phase, args=args)
        if args.event == 'init':
            if args.extra:
                raise context['error']('invalid_input', 'Choose an option before providing its parameters')
            return self.guidance(state, context)
        options = state.choices(context)
        self.validate_options(options)
        matches = [option for option in options if option.name == args.event]
        if not matches:
            raise context['error']('invalid_transition', 'Choose one of the returned options for ' + phase)
        parsed, errors = [], []
        for option in matches:
            parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
            for param in option.params:
                param.add_to(parser)
            try:
                values, unknown = parser.parse_known_args(args.extra)
                if unknown:
                    raise ValueError('Unknown option parameters: ' + ' '.join(unknown))
                for param in option.params:
                    name = param.flag.lstrip('-').replace('-', '_')
                    value = getattr(values, name)
                    if param.required and (value is None or value == [] or isinstance(value, str) and not value.strip()):
                        raise ValueError(param.flag + ' is required: ' + param.prompt)
                    if param.value is not None and str(value) != str(param.value):
                        raise ValueError('Use the returned ' + param.flag + ' value')
                parsed.append((option, values))
            except (argparse.ArgumentError, ValueError) as error:
                errors.append(str(error))
        if len(parsed) != 1:
            raise context['error']('invalid_input', errors[0] if errors else 'Choose a single returned option')
        option, values = parsed[0]
        vars(args).update(vars(values))
        result = option.action(args, context) if option.action else {}
        result = result or {}
        target = self.states[result.get('phase', option.target)] if result.get('accepted', True) else state
        return self.guidance(target, context, result)
