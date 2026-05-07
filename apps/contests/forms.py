from django import forms
from .models import Contest, Section, Question, TestCase


class ContestForm(forms.ModelForm):
    class Meta:
        model = Contest
        fields = ['title', 'description', 'start_time', 'end_time', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input'}),
            'description': forms.Textarea(attrs={'class': 'form-input', 'rows': 4}),
            'start_time': forms.DateTimeInput(
                attrs={'class': 'form-input', 'type': 'datetime-local'}
            ),
            'end_time': forms.DateTimeInput(
                attrs={'class': 'form-input', 'type': 'datetime-local'}
            ),
            'status': forms.Select(attrs={'class': 'form-input'}),
        }


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ['name', 'order', 'duration']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'order': forms.NumberInput(attrs={'class': 'form-input'}),
            'duration': forms.NumberInput(attrs={'class': 'form-input'}),
        }


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = [
            'title', 'problem_statement', 'input_format', 'output_format',
            'constraints', 'sample_input', 'sample_output',
            'marks', 'time_limit', 'order'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input'}),
            'problem_statement': forms.Textarea(attrs={'class': 'form-input', 'rows': 6}),
            'input_format': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'output_format': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'constraints': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'sample_input': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'sample_output': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'marks': forms.NumberInput(attrs={'class': 'form-input'}),
            'time_limit': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.5'}),
            'order': forms.NumberInput(attrs={'class': 'form-input'}),
        }


class TestCaseForm(forms.ModelForm):
    class Meta:
        model = TestCase
        fields = ['input_data', 'expected_output', 'is_hidden', 'order']
        widgets = {
            'input_data': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'expected_output': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
            'is_hidden': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'order': forms.NumberInput(attrs={'class': 'form-input'}),
        }


TestCaseFormSet = forms.inlineformset_factory(
    Question, TestCase,
    form=TestCaseForm,
    extra=2,
    can_delete=True
)